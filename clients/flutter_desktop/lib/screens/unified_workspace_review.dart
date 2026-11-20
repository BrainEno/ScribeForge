part of 'unified_workspace.dart';

extension _WorkspaceReview on _UnifiedWorkspaceState {
  (ReviewItemKey, String?) _draftKey(ReviewQueueItem item) =>
      (reviewItemKey(item), item.reviewToken);

  TextEditingController _reviewControllerFor(ReviewQueueItem item) =>
      _reviewControllers.putIfAbsent(
        _draftKey(item),
        TextEditingController.new,
      );

  void _disposeDraftsAfterFrame(Iterable<TextEditingController> controllers) {
    final detached = controllers.toList();
    if (detached.isEmpty) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      for (final controller in detached) {
        controller.dispose();
      }
    });
  }

  void _resetReviewForProject() {
    _reviewRequestVersion++;
    _reviewLoading = false;
    _reviewQueue.clear();
    _selectedReviewIndex = -1;
    _disposeDraftsAfterFrame(_reviewControllers.values);
    _reviewControllers.clear();
    _supersededDrafts.clear();
  }

  ReviewQueueItem? get _selectedReviewItem =>
      _selectedReviewIndex < 0 || _selectedReviewIndex >= _reviewQueue.length
      ? null
      : _reviewQueue[_selectedReviewIndex];

  Future<void> _refreshReviewQueue() async {
    final storePath = _projectStorePath;
    if (!mounted || storePath == null) return;
    final version = ++_reviewRequestVersion;
    final includeFurniture = _includePageFurnitureInReview;
    bool isCurrent() =>
        mounted &&
        version == _reviewRequestVersion &&
        storePath == _projectStorePath;

    _updateWorkspace(() => _reviewLoading = true);
    try {
      final databasePath = ScribeForgeCli.joinPath(
        storePath,
        'project.sqlite3',
      );
      final exists = await _fileSystem.fileExists(databasePath);
      if (!isCurrent()) return;
      if (!exists) {
        _updateWorkspace(() {
          _reviewQueue.clear();
          _selectedReviewIndex = -1;
        });
        return;
      }
      final output = StringBuffer();
      final diagnostics = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.reviewArguments(
          storePath: storePath,
          includePageFurniture: includeFurniture,
        ),
        onOutput: output.write,
        onError: diagnostics.write,
      );
      if (!isCurrent()) return;
      if (exitCode != 0) {
        final diagnostic = diagnostics.isEmpty
            ? output.toString()
            : diagnostics.toString();
        _updateWorkspace(
          () => _error = diagnostic.trim().isEmpty
              ? '读取复核队列失败（退出码 $exitCode）。'
              : diagnostic.trim(),
        );
        return;
      }
      final snapshot = ReviewQueueSnapshot.fromJsonText(output.toString());
      final visibleDrafts = <ReviewItemKey, String?>{
        for (final item in snapshot.items)
          reviewItemKey(item): item.reviewToken,
      };
      final previousVersions = <(ReviewItemKey, String?)>{
        for (final stored in _reviewControllers.keys)
          if (visibleDrafts.containsKey(stored.$1) &&
              visibleDrafts[stored.$1] != stored.$2)
            stored,
      };
      final detached = <TextEditingController>[];
      var hadOldText = false;
      for (final key in previousVersions) {
        final controller = _reviewControllers.remove(key);
        if (controller != null) {
          if (controller.text.isNotEmpty) {
            hadOldText = true;
            _supersededDrafts.remember(
              SupersededReviewDraft(
                key: key.$1,
                token: key.$2,
                text: controller.text,
              ),
            );
          }
          detached.add(controller);
        }
      }
      _disposeDraftsAfterFrame(detached);
      final selected = _selectedReviewItem;
      final key = selected == null ? null : reviewItemKey(selected);
      _updateWorkspace(() {
        _reviewQueue
          ..clear()
          ..addAll(snapshot.items);
        final retained = key == null
            ? -1
            : _reviewQueue.indexWhere((item) => reviewItemKey(item) == key);
        _selectedReviewIndex = retained >= 0
            ? retained
            : ReviewQueueNavigation.move(
                _selectedReviewIndex,
                itemCount: _reviewQueue.length,
                delta: 0,
              );
        _error = null;
        if (hadOldText) _notice = '核验版本已变化。旧草稿已单独保留，请重新核对。';
      });
    } catch (error) {
      if (isCurrent()) _updateWorkspace(() => _error = '读取复核队列失败：$error');
    } finally {
      if (isCurrent()) _updateWorkspace(() => _reviewLoading = false);
    }
  }

  // ---------------------------------------------------------------- actions

  Future<void> _resolveReviewPair(
    ReviewQueueItem item,
    String acceptedText,
  ) async {
    final storePath = _projectStorePath;
    final key = reviewItemKey(item);
    final token = item.reviewToken;
    if (storePath == null ||
        token == null ||
        _busy ||
        _reviewLoading ||
        !_reviewQueue.any(
          (candidate) =>
              reviewItemKey(candidate) == key && candidate.reviewToken == token,
        )) {
      return;
    }
    _updateWorkspace(() => _busy = true);
    try {
      final arguments = ScribeForgeCli.resolveArguments(
        storePath: storePath,
        projectId: item.projectId,
        page: item.pageIndex,
        pair: item.pairIndex,
        text: acceptedText,
        reviewToken: token,
      );
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted || storePath != _projectStorePath) return;
      if (exitCode == 0) {
        _updateWorkspace(() {
          _reviewQueue.removeWhere((row) => reviewItemKey(row) == key);
          _selectedReviewIndex = ReviewQueueNavigation.move(
            _selectedReviewIndex,
            itemCount: _reviewQueue.length,
            delta: 0,
          );
          final detached = _reviewControllers.remove(_draftKey(item));
          if (detached != null) _disposeDraftsAfterFrame([detached]);
          _notice =
              '已记录第 ${item.pageIndex + 1} 页第 ${item.pairIndex + 1} 组的人工决策';
          _error = null;
        });
        await _refreshStatus(waitForInFlight: true);
        _focusReviewDecisionInputAfterFrame();
      } else {
        _updateWorkspace(
          () => _error = '未记录人工决策。请复制必要的草稿内容，再刷新队列。${buffer.toString().trim()}',
        );
      }
    } catch (exc) {
      if (!mounted || storePath != _projectStorePath) return;
      _updateWorkspace(() => _error = '记录决策失败：$exc');
    } finally {
      if (mounted) _updateWorkspace(() => _busy = false);
    }
  }

  void _openReviewHistory(ReviewQueueItem? item) {
    final path = _projectStorePath;
    final project = item?.projectId ?? _projectStatus?.projectId;
    if (path == null || project == null || _busy) return;
    unawaited(
      showReviewHistory(
        context,
        service: ReviewHistoryService(
          executor: _cliExecutor,
          executable: _cliExecutable,
          storePath: path,
          projectId: project,
        ),
        filter: ReviewHistoryFilter(
          pageIndex: item?.pageIndex,
          pairIndex: item?.pairIndex,
        ),
      ),
    );
  }

  Future<void> _showSupersededDrafts() async {
    await showDialog<void>(
      context: context,
      builder: (_) => SupersededDraftsDialog(archive: _supersededDrafts),
    );
    if (mounted) _updateWorkspace(() {});
  }

  Widget _buildReviewPage() => ReviewWorkspacePage(
    storePath: _projectStorePath,
    projectId: _projectStatus?.projectId,
    queue: _reviewQueue,
    selectedIndex: _selectedReviewIndex,
    busy: _busy,
    loading: _reviewLoading,
    includePageFurniture: _includePageFurnitureInReview,
    decisionFocusNode: _reviewDecisionFocusNode,
    controllerFor: _reviewControllerFor,
    onRecord: _resolveReviewPair,
    onRefresh: _refreshReviewQueue,
    onHistory: _openReviewHistory,
    onSupersededDrafts: _supersededDrafts.isNotEmpty
        ? _showSupersededDrafts
        : null,
    onSelect: (index) => _updateWorkspace(() => _selectedReviewIndex = index),
    onIncludePageFurniture: (include) {
      _updateWorkspace(() => _includePageFurnitureInReview = include);
      unawaited(_refreshReviewQueue());
    },
  );

  void _focusReviewDecisionInputAfterFrame() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _selectedReviewItem != null) {
        _reviewDecisionFocusNode.requestFocus();
      }
    });
  }

  // ======================================================= 候选比较页面
}

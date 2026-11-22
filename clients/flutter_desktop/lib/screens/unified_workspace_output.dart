part of 'unified_workspace.dart';

extension _WorkspaceOutput on _UnifiedWorkspaceState {
  Future<void> _runExport({
    required String outputPath,
    required String language,
    required String title,
    String? author,
    String? modified,
    bool layoutChapter = true,
    bool strictAcceptance = false,
    bool includePageFurniture = false,
  }) async {
    if (_projectStorePath == null ||
        _projectStatus == null ||
        !_projectCanExport)
      return;
    _updateWorkspace(() {
      _busy = true;
      _error = null;
      _notice = null;
    });
    try {
      final arguments = ScribeForgeCli.exportArguments(
        storePath: _projectStorePath!,
        outputPath: outputPath,
        title: title,
        language: language,
        author: author,
        modified: modified,
        includePageFurniture: includePageFurniture,
      );
      // 布局与严格校验：CLI 支持 --layout 与 --strict-acceptance，这里拼接。
      final allArgs = <String>[...arguments];
      allArgs.addAll(<String>['--layout', layoutChapter ? 'chapter' : 'page']);
      if (strictAcceptance) allArgs.add('--strict-acceptance');

      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: allArgs,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0) {
        _updateWorkspace(() {
          _notice = '导出成功：$outputPath';
        });
      } else {
        _updateWorkspace(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      _updateWorkspace(() => _error = '导出失败：$exc');
    } finally {
      if (mounted) _updateWorkspace(() => _busy = false);
    }
  }

  Future<void> _inspectExportReadiness() async {
    final store = _projectStorePath;
    final projectId = _projectStatus?.projectId;
    if (store == null || projectId == null || _busy) return;
    final link = await Navigator.of(context).push<ExportReadinessLink>(
      MaterialPageRoute<ExportReadinessLink>(
        builder: (_) => ExportReadinessScreen(
          service: ExportReadinessService(
            executor: _cliExecutor,
            executable: _cliExecutable,
            storePath: store,
            projectId: projectId,
          ),
          options: ExportReadinessOptions(
            strict: _exportStrictAcceptance,
            includePageFurniture: _exportIncludePageFurniture,
          ),
          sourceMatches: _projectSourceMatches,
        ),
      ),
    );
    if (!mounted ||
        link == null ||
        _projectStorePath != store ||
        _projectStatus?.projectId != projectId)
      return;
    switch (link.destination) {
      case ExportReadinessDestination.processing:
        _updateWorkspace(() => _selectedIndex = 1);
      case ExportReadinessDestination.review:
        _updateWorkspace(() {
          _selectedIndex = 2;
          // Export also checks unresolved page furniture, even if normally
          // hidden by the review queue's default view.
          _includePageFurnitureInReview = true;
        });
        await _refreshReviewQueue();
        if (!mounted || _projectStorePath != store) return;
        final index = _reviewQueue.indexWhere(
          (item) =>
              item.projectId == projectId &&
              item.pageIndex == link.page &&
              item.pairIndex == link.pair,
        );
        _updateWorkspace(() {
          if (index >= 0) {
            _selectedReviewIndex = index;
          } else {
            _notice = '此配对不在最新待复核队列中。请重新运行导出前检查。';
          }
        });
      case ExportReadinessDestination.evidence:
        final page = link.page;
        if (page != null) {
          await showOcrEvidencePage(
            context,
            service: OcrEvidenceService(
              executor: _cliExecutor,
              executable: _cliExecutable,
              storePath: store,
              projectId: projectId,
            ),
            page: page,
          );
        }
      case ExportReadinessDestination.history:
        await showReviewHistory(
          context,
          service: ReviewHistoryService(
            executor: _cliExecutor,
            executable: _cliExecutable,
            storePath: store,
            projectId: projectId,
          ),
          filter: ReviewHistoryFilter(
            pageIndex: link.page,
            pairIndex: link.pair,
          ),
        );
      case ExportReadinessDestination.textless:
        final page = link.page;
        if (page == null) return;
        final saved = await Navigator.of(context).push<bool>(
          MaterialPageRoute<bool>(
            builder: (_) => TextlessConfirmationScreen(
              service: TextlessConfirmationService(
                executor: _cliExecutor,
                executable: _cliExecutable,
                storePath: store,
                project: projectId,
                page: page,
              ),
            ),
          ),
        );
        if (!mounted || _projectStorePath != store) return;
        await _refreshStatus(waitForInFlight: true);
        if (mounted && _projectStorePath == store && saved == true) {
          _updateWorkspace(
            () => _notice = '已记录第 ${page + 1} 页的无文字确认，可重新执行导出前检查。',
          );
        }
      case ExportReadinessDestination.safety:
        _updateWorkspace(() => _selectedIndex = 5);
    }
  }

  Future<void> _openExportVerification() => Navigator.of(context).push<void>(
    MaterialPageRoute<void>(
      builder: (_) => ExportVerificationScreen(
        service: ExportVerificationService(
          executor: _cliExecutor,
          executable: _cliExecutable,
        ),
        filePicker: _filePicker,
        lastOutput: _exportOutputController.text,
        sourceDigest: _projectStatus?.sourceSha256,
      ),
    ),
  );

  void _updateExportOutputPath() {
    final directory = _exportOutputDirectory;
    if (directory == null) return;
    var stem = _exportTitleController.text.trim();
    if (stem.isEmpty) stem = 'book';
    stem = stem.replaceAll(RegExp(r'[<>:"/\\|?*]'), '_');
    if (stem.isEmpty) stem = 'book';
    final extension = _exportFormatEpub ? 'epub' : 'docx';
    _exportOutputController.text = ScribeForgeCli.joinPath(
      directory,
      '$stem.$extension',
    );
  }

  Future<void> _pickExportOutput() async {
    final directory = await _filePicker.getDirectoryPath(
      dialogTitle: '选择导出文件夹',
    );
    if (!mounted || directory == null) return;
    _updateWorkspace(() {
      _exportOutputDirectory = directory;
      _updateExportOutputPath();
    });
  }

  Widget _buildCandidateComparisonPage() {
    final status = _projectStatus;
    final canGenerateCandidates =
        !_busy &&
        !_statusLoading &&
        _projectStorePath != null &&
        status != null &&
        _projectSourceMatches &&
        _projectCanExport &&
        status.pagesTotal > 0 &&
        status.runningJobs == 0 &&
        status.pendingJobs == 0 &&
        status.pagesWithBothOcrSuccess == status.pagesTotal;
    return CandidateComparisonScreen(
      sourcePdfPath: _pdfPath,
      storePath: _projectStorePath,
      cliExecutable: _cliExecutable,
      canGenerateCandidates: canGenerateCandidates,
      cliExecutor: _cliExecutor,
      filePicker: _filePicker,
      comparisonStore: const FileCandidateComparisonStore(),
      sourcePageCount: status?.importState == 'succeeded'
          ? status?.pagesTotal
          : null,
      expectedSourceSha256: status?.sourceSha256,
      onOpenTrustedReview: () {
        _updateWorkspace(() => _selectedIndex = 2);
        unawaited(_refreshReviewQueue());
      },
    );
  }

  // ========================================================= 导出页面

  Widget _buildExportPage() {
    return ScribePageBody(
      children: <Widget>[
        const ScribePageHeader(
          icon: Icons.publish_outlined,
          title: '导出成品',
          subtitle: '把已验证的文字写出为 EPUB 3 或 DOCX。先检查阻塞项，再生成文件。',
        ),
        const SizedBox(height: ScribeSpacing.x4),
        Wrap(
          spacing: 12,
          runSpacing: 8,
          children: <Widget>[
            OutlinedButton.icon(
              key: const ValueKey('open-export-readiness'),
              onPressed: _busy || _projectStatus == null
                  ? null
                  : _inspectExportReadiness,
              icon: const Icon(Icons.fact_check_outlined),
              label: const Text('导出前检查'),
            ),
            OutlinedButton.icon(
              key: const ValueKey('open-export-verification'),
              onPressed: _busy ? null : _openExportVerification,
              icon: const Icon(Icons.verified_outlined),
              label: const Text('校验已导出的文件'),
            ),
          ],
        ),
        const SizedBox(height: 20),
        _buildExportForm(),
      ],
    );
  }

  Widget _buildExportForm() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        ScribeAdaptivePair(
          primary: ScribeSectionCard(
            icon: Icons.menu_book_outlined,
            title: '文件与书籍信息',
            child: Column(
              children: <Widget>[
                DropdownButtonFormField<String>(
                  initialValue: _exportFormatEpub ? 'epub' : 'docx',
                  items: const <DropdownMenuItem<String>>[
                    DropdownMenuItem(value: 'epub', child: Text('EPUB 3')),
                    DropdownMenuItem(value: 'docx', child: Text('DOCX')),
                  ],
                  onChanged: (value) => _updateWorkspace(() {
                    _exportFormatEpub = value == 'epub';
                    _updateExportOutputPath();
                  }),
                  decoration: const InputDecoration(labelText: '导出格式'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _exportOutputController,
                  decoration: const InputDecoration(
                    labelText: '输出文件',
                    hintText: '选择导出文件夹，文件名按书名和格式生成',
                    suffixIcon: Icon(Icons.folder_open_outlined),
                  ),
                  readOnly: true,
                  onTap: _pickExportOutput,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _exportLanguageController,
                  decoration: const InputDecoration(
                    labelText: '语言（BCP 47，如 zh）',
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _exportTitleController,
                  decoration: const InputDecoration(labelText: '书名（可选）'),
                  onChanged: (_) => _updateWorkspace(_updateExportOutputPath),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _exportAuthorController,
                  decoration: const InputDecoration(labelText: '作者（可选）'),
                ),
                const SizedBox(height: 12),
              ],
            ),
          ),
          secondary: ScribeSectionCard(
            icon: Icons.tune,
            title: '版式与校验',
            child: Column(
              children: <Widget>[
                CheckboxListTile(
                  value: _exportLayoutChapter,
                  title: const Text('按章节布局（EPUB）'),
                  subtitle: const Text('关闭后每页一个文档（便于核对扫描）'),
                  onChanged: (v) =>
                      _updateWorkspace(() => _exportLayoutChapter = v ?? true),
                ),
                CheckboxListTile(
                  value: _exportStrictAcceptance,
                  title: const Text('严格接受校验'),
                  subtitle: const Text('有待抽查的配对时拒绝导出（退出码 2）'),
                  onChanged: (v) => _updateWorkspace(
                    () => _exportStrictAcceptance = v ?? false,
                  ),
                ),
                CheckboxListTile(
                  value: _exportIncludePageFurniture,
                  title: const Text('保留页眉、页脚与页码'),
                  subtitle: const Text('包含已识别的页边行；未裁决分歧仍会阻止可信导出。'),
                  onChanged: (v) => _updateWorkspace(
                    () => _exportIncludePageFurniture = v ?? false,
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 20),
        SizedBox(
          width: double.infinity,
          height: 48,
          child: FilledButton.icon(
            icon: Icon(_busy ? Icons.hourglass_empty : Icons.output),
            label: Text(_busy ? '导出中…' : '开始导出'),
            onPressed:
                _busy ||
                    _projectStorePath == null ||
                    _projectStatus == null ||
                    !_projectCanExport ||
                    _exportOutputController.text.isEmpty
                ? null
                : () => _runExport(
                    outputPath: _exportOutputController.text,
                    language: _exportLanguageController.text,
                    title: _exportTitleController.text,
                    author: _exportAuthorController.text,
                    layoutChapter: _exportLayoutChapter,
                    strictAcceptance: _exportStrictAcceptance,
                    includePageFurniture: _exportIncludePageFurniture,
                  ),
          ),
        ),
      ],
    );
  }

  // ========================================================= 项目安全页面
}

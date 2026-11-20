part of 'unified_workspace.dart';

extension _WorkspaceProject on _UnifiedWorkspaceState {
  Future<void> _refreshStatus({bool waitForInFlight = false}) async {
    if (_projectStorePath == null) return;
    if (_statusLoading) {
      if (!waitForInFlight) return;
      while (mounted && _statusLoading) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
      if (!mounted) return;
    }
    final storePath = _projectStorePath;
    final sourcePath = _pdfPath;
    if (storePath == null) return;
    final version = ++_statusRequestVersion;
    _updateWorkspace(() => _statusLoading = true);
    try {
      final databasePath = ScribeForgeCli.joinPath(
        storePath,
        'project.sqlite3',
      );
      if (!await _fileSystem.fileExists(databasePath)) {
        if (!mounted || version != _statusRequestVersion) return;
        _updateWorkspace(() {
          _projectStatus = null;
          _projectSourceMatches = true;
          _projectCanExport = true;
          _projectSourceMismatchMessage = null;
          _error = null;
        });
        return;
      }
      final arguments = ScribeForgeCli.statusArguments(storePath: storePath);
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted || version != _statusRequestVersion) return;
      if (exitCode != 0) {
        _updateWorkspace(() => _error = buffer.toString().trim());
        return;
      }

      final projects = ProjectStatusSnapshot.listFromJsonText(
        buffer.toString(),
      );
      var selected = sourcePath == null
          ? null
          : ProjectStatusSnapshot.fromJsonText(
              buffer.toString(),
              sourcePath: sourcePath,
            );
      if (selected == null && projects.length == 1) selected = projects.single;

      var sourceMatches = false;
      var exportAllowed = false;
      var mismatchMessage = '此存储目录没有与所选源文件匹配的唯一项目。';
      if (selected != null && sourcePath != null) {
        final pathMatches = selected.sourcePathMatches(sourcePath);
        final sourceExists = await _fileSystem.fileExists(sourcePath);
        final sourceHash = pathMatches && sourceExists
            ? await _sourceSha256(sourcePath)
            : null;
        final hashMatches =
            sourceHash != null && selected.sourceHashMatches(sourceHash);
        sourceMatches = projects.length == 1 && pathMatches && hashMatches;
        exportAllowed =
            projects.length == 1 &&
            pathMatches &&
            (!sourceExists || hashMatches);
        if (projects.length > 1) {
          mismatchMessage = '项目存储包含多个项目；请打开每本书各自的项目文件夹。';
        } else if (!pathMatches) {
          mismatchMessage = '所选源文件路径与项目记录不一致；请重新选择原文件或新建项目。';
        } else if (!sourceExists) {
          mismatchMessage = '找不到项目记录的源文件；请重新关联原文件。';
        } else if (!hashMatches) {
          mismatchMessage = '源文件内容与项目记录的 SHA-256 不一致，已阻止继续处理。';
        }
      }
      _updateWorkspace(() {
        _projectStatus = selected;
        _projectSourceMatches =
            projects.isEmpty || (selected != null && sourceMatches);
        _projectCanExport =
            projects.isEmpty || (selected != null && exportAllowed);
        _projectSourceMismatchMessage =
            projects.isEmpty || (selected != null && sourceMatches)
            ? null
            : mismatchMessage;
        if (selected != null && _exportTitleController.text.trim().isEmpty) {
          _exportTitleController.text = selected.title;
        }
        _error = null;
      });
    } catch (exc) {
      if (!mounted || version != _statusRequestVersion) return;
      _updateWorkspace(() => _error = '读取项目状态失败：$exc');
    } finally {
      if (mounted && version == _statusRequestVersion) {
        _updateWorkspace(() => _statusLoading = false);
      }
    }
  }

  void _clearSourceHashCache() {
    _sourceHashCachePath = null;
    _sourceHashCacheSize = null;
    _sourceHashCacheModifiedMillis = null;
    _sourceHashCacheValue = null;
  }

  Future<String?> _sourceSha256(String sourcePath) async {
    final stat = await _fileSystem.stat(sourcePath);
    if (stat == null) return null;
    final modifiedMillis = stat.modifiedMilliseconds;
    if (_sourceHashCachePath == sourcePath &&
        _sourceHashCacheSize == stat.size &&
        _sourceHashCacheModifiedMillis == modifiedMillis) {
      return _sourceHashCacheValue;
    }
    final digest = (await sha256.bind(_fileSystem.openRead(sourcePath)).first)
        .toString();
    _sourceHashCachePath = sourcePath;
    _sourceHashCacheSize = stat.size;
    _sourceHashCacheModifiedMillis = modifiedMillis;
    _sourceHashCacheValue = digest;
    return digest;
  }

  // ---------------------------------------------------------------- runtime

  Future<void> _pickPdf() async {
    final sourcePath = await _filePicker.pickFile(
      allowedExtensions: <String>['pdf', 'png', 'jpg', 'jpeg'],
    );
    if (!mounted || sourcePath == null) return;
    final storePath = ScribeForgeCli.defaultStorePath(sourcePath);
    final filename = File(sourcePath).uri.pathSegments.last;
    final extension = filename.lastIndexOf('.');
    final title = extension > 0 ? filename.substring(0, extension) : filename;
    _clearSourceHashCache();
    _updateWorkspace(() {
      _statusRequestVersion++;
      _statusLoading = false;
      _pdfPath = sourcePath;
      _exportTitleController.text = title;
      _exportOutputDirectory = null;
      _exportOutputController.clear();
      _projectStorePath = storePath;
      _projectSourceMatches = true;
      _projectCanExport = true;
      _projectSourceMismatchMessage = null;
      _projectStatus = null;
      _resetReviewForProject();
      _error = null;
      _notice = null;
    });
    _rememberProjectStore(storePath);
    await _refreshStatus();
    await _refreshReviewQueue();
  }

  void _rememberProjectStore(String storePath) {
    unawaited(
      _settingsStore.saveLastProjectStore(storePath).then((result) {
        if (!result.ok && mounted) {
          _updateWorkspace(() => _error = '保存最近项目失败：${result.error}');
        }
      }),
    );
  }

  Future<void> _openProject() async {
    final storePath = await _filePicker.getDirectoryPath(
      dialogTitle: '选择 ScribeForge 项目文件夹',
    );
    if (!mounted || storePath == null) return;
    await _openProjectAt(storePath);
  }

  /// Validates and loads the project store at [storePath]; shared by the
  /// file picker and the one-click "继续上次项目" resume action.
  Future<void> _openProjectAt(String storePath) async {
    final databasePath = ScribeForgeCli.joinPath(storePath, 'project.sqlite3');
    if (!await _fileSystem.fileExists(databasePath)) {
      _updateWorkspace(() => _error = '所选文件夹中没有 ScribeForge 项目数据库。');
      return;
    }
    _clearSourceHashCache();
    _updateWorkspace(() {
      _statusRequestVersion++;
      _statusLoading = false;
      _projectStorePath = storePath;
      _pdfPath = null;
      _exportOutputDirectory = null;
      _exportOutputController.clear();
      _projectStatus = null;
      _projectSourceMatches = false;
      _projectCanExport = false;
      _projectSourceMismatchMessage = null;
      _resetReviewForProject();
      _error = null;
    });
    try {
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.statusArguments(storePath: storePath),
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted) return;
      if (exitCode != 0) {
        _updateWorkspace(() => _error = buffer.toString().trim());
        return;
      }
      final projects = ProjectStatusSnapshot.listFromJsonText(
        buffer.toString(),
      );
      if (projects.length != 1) {
        _updateWorkspace(
          () => _error = projects.isEmpty
              ? '所选项目文件夹还没有导入书籍。'
              : '该文件夹包含多个项目；请打开每本书各自的项目文件夹。',
        );
        return;
      }
      _updateWorkspace(() {
        _pdfPath = projects.single.sourceUri;
        _projectStatus = projects.single;
        _exportTitleController.text = projects.single.title;
        _selectedIndex = 0;
      });
      _rememberProjectStore(storePath);
      await _refreshStatus();
      await _refreshReviewQueue();
    } catch (exc) {
      if (!mounted) return;
      _updateWorkspace(() => _error = '打开项目失败：$exc');
    }
  }

  Future<void> _chooseProjectStoreDirectory() async {
    final storePath = await _filePicker.getDirectoryPath(
      dialogTitle: '选择项目存储目录',
    );
    if (!mounted || storePath == null) return;
    _updateWorkspace(() {
      _statusRequestVersion++;
      _statusLoading = false;
      _projectStorePath = storePath;
      _projectStatus = null;
      _exportOutputDirectory = null;
      _exportOutputController.clear();
      _projectSourceMatches = true;
      _projectCanExport = true;
      _projectSourceMismatchMessage = null;
      _resetReviewForProject();
    });
    await _refreshStatus();
    await _refreshReviewQueue();
  }

  Future<void> _relinkSource() async {
    final storePath = _projectStorePath;
    if (storePath == null) return;
    final sourcePath = await _filePicker.pickFile(
      dialogTitle: '重新关联原始 PDF 或页面图像',
      allowedExtensions: <String>['pdf', 'png', 'jpg', 'jpeg'],
    );
    if (!mounted || sourcePath == null) return;
    _updateWorkspace(() => _busy = true);
    try {
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.relinkSourceArguments(
          storePath: storePath,
          sourcePath: sourcePath,
        ),
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted) return;
      if (exitCode == 0) {
        _clearSourceHashCache();
        _updateWorkspace(() {
          _pdfPath = sourcePath;
          _projectSourceMatches = true;
          _projectCanExport = true;
          _projectSourceMismatchMessage = null;
          _notice = '原始文件已重新关联；SHA-256 匹配，现有处理证据未更改。';
          _error = null;
        });
        await _refreshStatus();
      } else {
        _updateWorkspace(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      _updateWorkspace(() => _error = '重新关联源文件失败：$exc');
    } finally {
      if (mounted) _updateWorkspace(() => _busy = false);
    }
  }

  Widget _buildLibraryPage() {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text(
            '已导入的书籍',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          Text(
            '导入新书，或打开已有项目继续处理、复核和导出。',
            style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 10,
            runSpacing: 8,
            children: <Widget>[
              ElevatedButton.icon(
                icon: const Icon(Icons.add),
                label: const Text('导入书籍'),
                onPressed: _busy
                    ? null
                    : () async {
                        await _pickPdf();
                        if (mounted && _pdfPath != null) {
                          _updateWorkspace(() => _selectedIndex = 1);
                        }
                      },
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.folder_open),
                label: const Text('打开项目文件夹'),
                onPressed: _busy ? null : _openProject,
              ),
            ],
          ),
          const SizedBox(height: 20),
          if (_statusLoading && _projectStatus == null)
            const LinearProgressIndicator()
          else if (_projectStatus == null)
            const Text('尚未选择书籍或打开项目。', style: TextStyle(color: Colors.grey))
          else
            _buildProjectStatusCard(),
        ],
      ),
    );
  }

  String _stageLabel(String stage) => switch (stage) {
    'import' => '导入页面',
    'preprocess' => '图像预处理',
    'primary_ocr' => 'OCR 引擎一',
    'secondary_ocr' => 'OCR 引擎二',
    'verify' => '比对与风险评估',
    'vlm_review' => '视觉模型复核',
    _ => stage,
  };

  Widget _buildProjectStatusCard() {
    final status = _projectStatus!;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    status.title,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                if (!_projectSourceMatches)
                  const Icon(Icons.warning, color: Colors.amber, size: 18),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              status.sourceUri,
              style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
            ),
            const SizedBox(height: 12),
            LinearProgressIndicator(value: status.ocrProgress),
            const SizedBox(height: 6),
            Text(
              '${status.pagesWithBothOcrSuccess} / ${status.pagesTotal} 页已完成双引擎 OCR',
              style: const TextStyle(fontSize: 12),
            ),
            const SizedBox(height: 8),
            Text(
              '任务：${status.succeededJobs} 已完成 · ${status.pendingJobs} 等待 · '
              '${status.runningJobs} 运行中 · ${status.failedJobs} 失败',
              style: const TextStyle(fontSize: 12),
            ),
            if (status.currentJob != null) ...[
              const SizedBox(height: 6),
              Text(
                '当前页面任务：${_stageLabel(status.currentJob!.stage)}'
                '${status.currentJob!.pageIndex == null ? '' : ' · 第 ${status.currentJob!.pageIndex! + 1} 页'}',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.primary,
                  fontSize: 12,
                ),
              ),
            ],
            const SizedBox(height: 8),
            Text(
              '复核队列：${status.reviewQueued} 项待处理',
              style: const TextStyle(fontSize: 12),
            ),
            if (!_projectSourceMatches &&
                _projectSourceMismatchMessage != null) ...[
              const SizedBox(height: 8),
              Text(
                _projectSourceMismatchMessage!,
                style: TextStyle(color: Colors.amber.shade800, fontSize: 12),
              ),
              TextButton.icon(
                onPressed: _busy ? null : _relinkSource,
                icon: const Icon(Icons.link, size: 16),
                label: const Text('重新关联源文件（需 SHA-256 相同）'),
              ),
            ],
            if (status.failedJobDetails.isNotEmpty) ...[
              const SizedBox(height: 8),
              const Text(
                '失败任务：',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
              ),
              ...status.failedJobDetails.map(
                (failed) => Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    failed.description,
                    style: TextStyle(color: Colors.red.shade700, fontSize: 11),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  // ========================================================= 处理页面

  Widget _buildProjectSafetyPage() {
    var title = _projectStatus?.title ?? '';
    if (title.isEmpty && _pdfPath != null) {
      final filename = File(_pdfPath!).uri.pathSegments.last;
      final extension = filename.lastIndexOf('.');
      title = extension > 0 ? filename.substring(0, extension) : filename;
    }
    return ProjectSafetyScreen(
      storePath: _projectStorePath,
      projectTitle: title,
      cliExecutable: _cliExecutable,
      cliExecutor: _cliExecutor,
      filePicker: _filePicker,
      fileSystem: _fileSystem,
      onProjectRestored: _handleProjectRestored,
    );
  }

  Future<void> _handleProjectRestored(String storePath) async {
    _clearSourceHashCache();
    _updateWorkspace(() {
      _statusRequestVersion++;
      _statusLoading = false;
      _projectStorePath = storePath;
      _pdfPath = null;
      _projectStatus = null;
      _projectSourceMatches = true;
      _projectCanExport = true;
      _projectSourceMismatchMessage = null;
      _exportTitleController.clear();
      _exportOutputController.clear();
      _exportOutputDirectory = null;
      _resetReviewForProject();
      _selectedIndex = 0;
      _error = null;
      _notice = '项目已恢复并载入工作台。';
    });
    _rememberProjectStore(storePath);
    await _refreshStatus();
    if (!mounted) return;
    final restoredSourcePath = _projectStatus?.sourceUri;
    if (restoredSourcePath != null && restoredSourcePath.isNotEmpty) {
      _updateWorkspace(() => _pdfPath = restoredSourcePath);
      await _refreshStatus();
    }
    await _refreshReviewQueue();
  }

  // The settings feature composes callbacks without bypassing the widget lifecycle.
}

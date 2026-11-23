part of 'unified_workspace.dart';

extension _WorkspaceProcessing on _UnifiedWorkspaceState {
  Future<void> _refreshRuntimeStatus() async {
    _updateWorkspace(() {
      _runtimeLoading = true;
      _runtimeHealthReport = null;
    });
    var commandExited = false;
    try {
      final arguments = ScribeForgeCli.runtimeStatusArguments();
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      commandExited = true;
      if (!mounted) return;
      if (exitCode == 0) {
        final status = RuntimeStatusSnapshot.fromJsonText(buffer.toString());
        _updateWorkspace(() {
          _backendConnected = true;
          _backendLaunchError = null;
          _runtimeStatus = status;
          _runtimeStatusError = null;
          _runtimeSetupActionRequired = !_ocrRuntimeReady;
          _error = null;
        });
      } else {
        _updateWorkspace(() {
          _backendConnected = true;
          _backendLaunchError = null;
          _runtimeStatus = null;
          _runtimeStatusError = buffer.toString().trim().isEmpty
              ? '检测命令退出码 $exitCode，未返回详细信息。'
              : buffer.toString().trim();
          _runtimeSetupActionRequired = true;
          _error = buffer.toString().trim();
        });
      }
    } catch (exc) {
      if (!mounted) return;
      final diagnosis = diagnoseCoreLaunchFailure(exc, _cliExecutable);
      _updateWorkspace(() {
        _backendConnected = commandExited;
        _backendLaunchError = commandExited ? null : exc;
        _runtimeStatus = null;
        _runtimeStatusError = commandExited
            ? '读取运行时状态失败：$exc'
            : diagnosis.message;
        _runtimeSetupActionRequired = diagnosis.requiresRuntimeSetup;
        _error = commandExited ? '读取运行时状态失败：$exc' : diagnosis.message;
      });
    } finally {
      if (mounted) {
        _updateWorkspace(() {
          _runtimeLoading = false;
          _runtimeChecked = true;
        });
      }
    }
  }

  // ------------------------------------------------------------- review queue

  Future<void> _cancelProcessing() async {
    if (!_processing || _cancelRequested) return;
    _updateWorkspace(() {
      _cancelRequested = true;
      _error = null;
      _notice = '正在请求取消处理…';
    });
    try {
      await _cliExecutor.cancel();
    } catch (exc) {
      if (!mounted) return;
      _updateWorkspace(() {
        _cancelRequested = false;
        _notice = null;
        _error = '发送取消请求失败：$exc';
      });
    }
  }

  String? _processBlockedReason() {
    if (_processing) return _cancelRequested ? '正在取消，请稍候。' : '正在处理，可以取消并稍后继续。';
    if (_busy) return '其他操作正在进行，请等待完成后再处理。';
    if (_runtimeLoading) return '正在检测处理环境，请稍候。';
    if (_statusLoading) return '正在检查项目和原文件，请稍候；大文件校验可能需要一些时间。';
    if (_pdfPath == null) return '请先选择 PDF、JPG 或 PNG 文件。';
    if (!_projectSourceMatches) {
      return _projectSourceMismatchMessage ?? '项目与所选源文件不匹配，暂不能处理。';
    }
    if (!_backendConnected) return '处理核心不可用；点击开始可查看修复指引。';
    if (_runtimeStatus == null) return '环境检测失败，状态未知；点击开始可查看配置。';
    return null;
  }

  Future<void> _runProcess({List<int>? pageIndexes}) async {
    final sourcePath = _pdfPath;
    if (sourcePath == null ||
        _statusLoading ||
        _runtimeLoading ||
        !_projectSourceMatches) {
      return;
    }
    if (!_backendConnected) {
      _updateWorkspace(() {
        _selectedIndex = 6;
        _runtimeSetupActionRequired = true;
        _error = diagnoseCoreLaunchFailure(
          _backendLaunchError ?? StateError('backend is unavailable'),
          _cliExecutable,
        ).message;
      });
      return;
    }
    if (!_ocrRuntimeReady) {
      _updateWorkspace(() {
        _selectedIndex = 6;
        _runtimeSetupActionRequired = true;
        _notice = 'PDF 已保留在当前工作台。请先初始化 OCR 环境，再开始处理。';
      });
      await _openRuntimeSetup();
      return;
    }
    final storePath =
        _projectStorePath ?? ScribeForgeCli.defaultStorePath(sourcePath);
    _updateWorkspace(() {
      _busy = true;
      _processing = true;
      _cancelRequested = false;
      _projectStorePath = storePath;
      _error = null;
      _notice = pageIndexes == null
          ? '处理中；正在读取页级任务状态…'
          : '正在续跑所选物理页；正在读取页级任务状态…';
    });
    _processStatusTimer?.cancel();
    _processStatusTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && _busy && !_statusLoading) unawaited(_refreshStatus());
    });
    unawaited(_refreshStatus());
    try {
      final arguments = ScribeForgeCli.processArguments(
        pdfPath: sourcePath,
        storePath: storePath,
        vlmSettings: _vlmSettings,
        cancelable: true,
        pageWorkers: _pageWorkers,
        pageIndexes: pageIndexes,
      );
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted) return;
      if (exitCode == 130) {
        await _refreshStatus(waitForInFlight: true);
        await _refreshReviewQueue();
        if (!mounted) return;
        _updateWorkspace(() {
          _error = null;
          _notice = '处理已取消；已保存的页面证据和人工决策保留，可重试失败任务。';
        });
      } else if (exitCode == 0 || exitCode == 1) {
        await _refreshStatus(waitForInFlight: true);
        await _refreshReviewQueue();
        _updateWorkspace(() {
          _notice = pageIndexes == null
              ? (exitCode == 0 ? '处理完成' : '处理完成（部分页面失败；请查看页级状态后重试）')
              : (exitCode == 0
                    ? '所选物理页续跑完成；不代表全书已完成。'
                    : '所选物理页续跑结束，仍有页面失败；请查看失败历史。');
        });
      } else {
        final diagnosis = diagnoseProcessingFailure(buffer.toString());
        _updateWorkspace(() {
          _runtimeSetupActionRequired = diagnosis.requiresRuntimeSetup;
          _error = diagnosis.message;
        });
      }
    } catch (exc) {
      if (!mounted) return;
      final diagnosis = diagnoseCoreLaunchFailure(exc, _cliExecutable);
      _updateWorkspace(() {
        _runtimeSetupActionRequired = diagnosis.requiresRuntimeSetup;
        _error = diagnosis.message;
      });
    } finally {
      _processStatusTimer?.cancel();
      _processStatusTimer = null;
      if (mounted) {
        _updateWorkspace(() {
          _busy = false;
          _processing = false;
          _cancelRequested = false;
        });
      }
    }
  }

  Future<void> _showScopedRetry({List<int> initialPages = const []}) async {
    final originalStore = _projectStorePath;
    final originalSource = _pdfPath;
    final project = _projectStatus;
    if (_busy ||
        _statusLoading ||
        _runtimeLoading ||
        originalStore == null ||
        originalSource == null ||
        project == null ||
        !_projectSourceMatches ||
        project.importState != 'succeeded') {
      return;
    }
    final selection = await showDialog<List<int>>(
      context: context,
      builder: (_) => ScopedRetryDialog(
        initialPages: initialPages,
        sourcePages: project.sourcePagesTotal,
        remoteVlmEnabled: _vlmSettings.enabled,
      ),
    );
    if (!mounted || selection == null) return;
    // Status and source identity can change while a dialog is open. Recheck
    // before invoking a mutating command; the CLI verifies the source again.
    await _refreshStatus(waitForInFlight: true);
    if (!mounted) return;
    if (_busy ||
        _projectStorePath != originalStore ||
        _pdfPath != originalSource ||
        !_projectSourceMatches ||
        _projectStatus?.projectId != project.projectId ||
        _projectStatus?.importState != 'succeeded') {
      _updateWorkspace(() => _error = '项目或原文件状态已变化，未开始续跑。请重新检查项目。');
      return;
    }
    await _runProcess(pageIndexes: selection);
  }

  Future<void> _openJobHistory() async {
    final store = _projectStorePath;
    final project = _projectStatus;
    if (_busy || store == null || project == null) return;
    final selected = await Navigator.of(context).push<int>(
      MaterialPageRoute(
        builder: (_) => JobHistoryScreen(
          service: JobHistoryService(
            executor: _cliExecutor,
            executable: _cliExecutable,
            storePath: store,
            projectId: project.projectId,
          ),
          canSelectPage:
              _projectSourceMatches && project.importState == 'succeeded',
        ),
      ),
    );
    if (!mounted || selected == null) return;
    if (_projectStorePath != store ||
        _projectStatus?.projectId != project.projectId) {
      _updateWorkspace(() => _error = '项目已变化，未开始续跑。');
      return;
    }
    await _showScopedRetry(initialPages: [selected]);
  }

  Future<void> _openVlmSettings() async {
    final settings = await showDialog<DesktopVlmSettings>(
      context: context,
      builder: (context) => VlmSettingsDialog(
        executable: _cliExecutable,
        initial: _vlmSettings,
        cliExecutor: _cliExecutor,
      ),
    );
    if (!mounted || settings == null) return;
    try {
      final result = await _settingsStore.saveVlmSettings(settings);
      if (!mounted) return;
      if (!result.ok) {
        final message = '保存 VLM 设置失败：${result.error}。可重新打开配置后重试。';
        _updateWorkspace(() => _error = message);
        return;
      }
      var notice = settings.enabled
          ? 'VLM 已启用；处理时会按此配置发送复核裁切图。'
          : 'VLM 已关闭；处理不会发送复核裁切图。';
      _updateWorkspace(() {
        _vlmSettings = settings;
        _notice = notice;
        _error = null;
      });
      // Issue #217: after saving, probe connectivity with the built-in sample
      // crop so a misconfigured endpoint is caught immediately. The API key
      // itself is never persisted — only the environment variable name.
      if (settings.enabled && settings.isConfigured) {
        final probe = await _probeVlmConnectivity(settings);
        if (mounted && probe != null) {
          _updateWorkspace(() => _notice = '$notice$probe');
        }
      }
    } on Object catch (error) {
      if (!mounted) return;
      _updateWorkspace(() => _error = '保存 VLM 设置失败：$error');
    }
  }

  /// Runs `vlm-check` once with the built-in sample crop (issue #217).
  ///
  /// Returns a short Chinese suffix describing the outcome, or null when the
  /// probe cannot run (e.g. CLI missing) so the save notice stays unchanged.
  Future<String?> _probeVlmConnectivity(DesktopVlmSettings settings) async {
    try {
      final sample = await writeBuiltInVlmSampleImage();
      var output = '';
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.vlmCheckArguments(
          imagePath: sample.path,
          vlmSettings: settings,
        ),
        onOutput: (chunk) => output += chunk,
        onError: (chunk) => output += chunk,
      );
      if (exitCode == 0) {
        return ' 连通性探测成功。';
      }
      final detail = output.trim().isEmpty ? '' : '：${output.trim()}';
      return ' 连通性探测失败（退出码 $exitCode）$detail';
    } on Object {
      return null;
    }
  }

  Future<void> _openRuntimeSetup({bool autoStart = false}) async {
    if (!_backendConnected) {
      _updateWorkspace(() {
        _selectedIndex = 6;
        _runtimeSetupActionRequired = true;
        _error = diagnoseCoreLaunchFailure(
          _backendLaunchError ?? StateError('backend is unavailable'),
          _cliExecutable,
        ).message;
      });
      return;
    }
    final attempted = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => RuntimeSetupDialog(
        runtimeStatus: _runtimeStatus,
        runSetup: _executeSetup,
        cancelSetup: _cliExecutor.cancel,
        autoStart: autoStart,
      ),
    );
    if (!mounted || attempted != true) return;
    await _refreshRuntimeStatus();
    if (!mounted || !_ocrRuntimeReady) return;
    _continueAfterRuntimeSetup();
  }

  void _continueAfterRuntimeSetup() {
    _updateWorkspace(() {
      _selectedIndex = _pdfPath == null ? 0 : 1;
      _notice = '环境已就绪，可以继续导入或处理书籍。';
      _runtimeSetupActionRequired = false;
    });
  }

  Future<int> _executeSetup({
    required String? backend,
    required void Function(RuntimeSetupMessage message) onMessage,
    required void Function(String diagnostic) onDiagnostic,
  }) async {
    var pending = '';

    void parseLine(String line) {
      if (line.trim().isEmpty) return;
      try {
        onMessage(RuntimeSetupMessage.fromJsonLine(line));
      } on FormatException catch (error) {
        onDiagnostic('无法解析运行时进度：$error');
      }
    }

    void receiveChunk(String chunk) {
      pending += chunk;
      var lineEnd = pending.indexOf('\n');
      while (lineEnd >= 0) {
        parseLine(pending.substring(0, lineEnd).replaceAll('\r', ''));
        pending = pending.substring(lineEnd + 1);
        lineEnd = pending.indexOf('\n');
      }
    }

    try {
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.setupArguments(backend: backend),
        onOutput: receiveChunk,
        onError: onDiagnostic,
      );
      if (pending.trim().isNotEmpty) parseLine(pending.trim());
      return exitCode;
    } on Object catch (error) {
      onDiagnostic('无法启动 ScribeForge 命令行工具：$error');
      rethrow;
    }
  }

  // ------------------------------------------------------------------ build

  Widget _buildProcessPage() {
    final hasFailedJobs = (_projectStatus?.failedJobs ?? 0) > 0;
    final blockedReason = _processBlockedReason();
    return ScribePageBody(
      children: <Widget>[
        const ScribePageHeader(
          icon: Icons.document_scanner_outlined,
          title: '导入并处理书籍',
          subtitle: '选择扫描 PDF 或页面图片，运行可恢复的双引擎 OCR。',
        ),
        const SizedBox(height: ScribeSpacing.x5),
        ScribeSectionCard(
          icon: Icons.description_outlined,
          title: '源文件与识别',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Text(
                this._engineComboSummary(),
                key: const ValueKey('engine-combo-summary'),
                style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
              ),
              const SizedBox(height: 20),
              WorkspaceFileField(
                field: InputDecorator(
                  decoration: const InputDecoration(
                    labelText: '源文件',
                    prefixIcon: Icon(Icons.description),
                  ),
                  child: Text(
                    _pdfPath ?? '尚未选择源文件',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                action: OutlinedButton.icon(
                  icon: const Icon(Icons.folder_open),
                  label: const Text('选择文件'),
                  onPressed: _busy ? null : _pickPdf,
                ),
              ),
              const SizedBox(height: 16),
              const Text(
                '中断后重新点击开始处理，将继续未完成的导入和页级任务；已完成阶段与人工决定不会主动重跑。',
                style: TextStyle(fontSize: 12),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<int>(
                key: const ValueKey('page-worker-count'),
                initialValue: _pageWorkers,
                decoration: const InputDecoration(labelText: '同时处理的页数'),
                items: const <DropdownMenuItem<int>>[
                  DropdownMenuItem(value: 1, child: Text('1 页（默认，最省资源）')),
                  DropdownMenuItem(value: 2, child: Text('2 页')),
                  DropdownMenuItem(value: 3, child: Text('3 页')),
                  DropdownMenuItem(value: 4, child: Text('4 页')),
                ],
                onChanged: _busy
                    ? null
                    : (value) => _updateWorkspace(() {
                        if (value != null) _pageWorkers = value;
                      }),
              ),
              const SizedBox(height: 6),
              const Text(
                '并行可利用更多 CPU，但会同时启动多个 OCR 模型，占用更多内存和显存；不一定更快。资源不足时改回 1 页后继续。',
                style: TextStyle(fontSize: 12),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  icon: Icon(
                    _processing
                        ? (_cancelRequested
                              ? Icons.hourglass_empty
                              : Icons.cancel_outlined)
                        : (_busy ? Icons.hourglass_empty : Icons.play_arrow),
                  ),
                  label: Text(
                    _processing
                        ? (_cancelRequested ? '正在取消…' : '取消处理')
                        : (_busy
                              ? '处理中…'
                              : (hasFailedJobs ? '重试失败页面' : '开始处理')),
                  ),
                  onPressed: _processing
                      ? (_cancelRequested ? null : _cancelProcessing)
                      : (_busy ||
                                _statusLoading ||
                                _runtimeLoading ||
                                _pdfPath == null ||
                                !_projectSourceMatches
                            ? null
                            : _runProcess),
                  style: FilledButton.styleFrom(
                    minimumSize: const Size.fromHeight(48),
                    backgroundColor: _processing ? Colors.red.shade700 : null,
                    foregroundColor: _processing ? Colors.white : null,
                  ),
                ),
              ),
              const SizedBox(height: 10),
              Text(
                blockedReason ??
                    (_ocrRuntimeReady
                        ? '可以开始处理；首次运行会导入页面并保存项目。'
                        : '处理前需要准备运行环境；点击开始可查看配置和下载信息。'),
                key: const ValueKey('process-readiness-message'),
                style: TextStyle(
                  color: blockedReason == null
                      ? Colors.grey.shade700
                      : Colors.orange.shade900,
                ),
              ),
              if (!_projectSourceMatches && !_statusLoading && !_busy) ...[
                const SizedBox(height: 4),
                Wrap(
                  spacing: 8,
                  children: [
                    TextButton.icon(
                      onPressed: () => unawaited(_refreshStatus()),
                      icon: const Icon(Icons.refresh),
                      label: const Text('重试项目检查'),
                    ),
                    TextButton.icon(
                      onPressed: _relinkSource,
                      icon: const Icon(Icons.link),
                      label: const Text('重新关联原文件'),
                    ),
                  ],
                ),
              ],
              if (_projectStatus != null) ...[
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: [
                    OutlinedButton.icon(
                      key: const ValueKey('open-job-history'),
                      onPressed: _busy ? null : _openJobHistory,
                      icon: const Icon(Icons.history),
                      label: const Text('查看失败与中断历史'),
                    ),
                    OutlinedButton.icon(
                      key: const ValueKey('scoped-retry-button'),
                      onPressed:
                          _busy ||
                              _statusLoading ||
                              _runtimeLoading ||
                              !_projectSourceMatches ||
                              _projectStatus?.importState != 'succeeded'
                          ? null
                          : () => _showScopedRetry(),
                      icon: const Icon(Icons.filter_1_outlined),
                      label: const Text('按物理页续跑'),
                    ),
                  ],
                ),
                if (_projectStatus!.importState != 'succeeded')
                  const Text('按页续跑要求原书已完整导入；请先继续整书导入。'),
              ],
              if (_busy) ...[
                const SizedBox(height: 12),
                const LinearProgressIndicator(),
                const SizedBox(height: 6),
                Text(
                  _projectStatus?.currentJob == null
                      ? '正在导入页面或准备 OCR 运行时…'
                      : '当前任务：${_stageLabel(_projectStatus!.currentJob!.stage)}'
                            '${_projectStatus!.currentJob!.pageIndex == null ? '' : ' · 第 ${_projectStatus!.currentJob!.pageIndex! + 1} 页'}',
                  style: TextStyle(color: Colors.grey.shade700, fontSize: 12),
                ),
              ],
              if (_pdfPath != null) ...[
                const SizedBox(height: 16),
                const Text(
                  '项目存储目录：',
                  style: TextStyle(fontSize: 12, color: Colors.grey),
                ),
                const SizedBox(height: 4),
                SelectableText(
                  _projectStorePath ??
                      ScribeForgeCli.defaultStorePath(_pdfPath!),
                  style: TextStyle(fontSize: 11, color: Colors.grey.shade600),
                ),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: _busy ? null : _chooseProjectStoreDirectory,
                    icon: const Icon(Icons.drive_file_move_outline, size: 16),
                    label: const Text('更改项目存储目录'),
                  ),
                ),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    key: const ValueKey('open-report-button'),
                    onPressed: _showProcessingReport,
                    icon: const Icon(Icons.assessment_outlined, size: 16),
                    label: const Text('查看处理报告'),
                  ),
                ),
              ],
            ],
          ),
        ),
        if (_projectStatus != null) ...[
          const SizedBox(height: 16),
          _buildProjectStatusCard(),
        ],
      ],
    );
  }

  void _showProcessingReport() {
    final storePath =
        _projectStorePath ??
        (_pdfPath == null ? null : ScribeForgeCli.defaultStorePath(_pdfPath!));
    if (storePath == null) return;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (context) => ProcessingReportSheet(
        executable: _cliExecutable,
        storePath: storePath,
        loader: _reportLoader,
        projectId: _projectStatus?.projectId,
        compareHandler: () => Navigator.of(context).push<void>(
          MaterialPageRoute<void>(
            builder: (_) => ReportComparisonScreen(
              service: ReportComparisonService(
                executor: _cliExecutor,
                executable: _cliExecutable,
              ),
              picker: _filePicker,
            ),
          ),
        ),
        processing: _processing,
        exportHandler: _exportProcessingReport,
      ),
    );
  }

  Future<String?> _exportProcessingReport(
    ProcessingReportSnapshot report, {
    required bool markdown,
  }) async {
    final directory = await _filePicker.getDirectoryPath(
      dialogTitle: '选择报告导出目录',
    );
    if (!mounted || directory == null || directory.trim().isEmpty) {
      return null;
    }
    final stamp = DateTime.now()
        .toIso8601String()
        .replaceAll(RegExp(r'[:.]'), '-')
        .substring(0, 19);
    final fileName = markdown ? 'report-$stamp.md' : 'report-$stamp.json';
    final file = File(ScribeForgeCli.joinPath(directory.trim(), fileName));
    await file.writeAsString(
      markdown ? processingReportMarkdown(report) : report.rawJson,
    );
    return file.path;
  }
}

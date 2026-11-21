part of 'unified_workspace.dart';

extension _WorkspaceProcessing on _UnifiedWorkspaceState {
  Future<void> _refreshRuntimeStatus() async {
    _updateWorkspace(() => _runtimeLoading = true);
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
          _runtimeSetupActionRequired = !_ocrRuntimeReady;
          _error = null;
        });
      } else {
        _updateWorkspace(() {
          _backendConnected = true;
          _backendLaunchError = null;
          _runtimeStatus = null;
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

  Future<void> _runProcess() async {
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
      await _openRuntimeSetup(autoStart: true);
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
      _notice = '处理中；正在读取页级任务状态…';
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
          _notice = exitCode == 0 ? '处理完成' : '处理完成（部分页面失败；请查看页级状态后重试）';
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
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text(
            '导入并处理书籍',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          Text(
            '选择扫描书籍的 PDF 或页面图片，运行可恢复的双引擎 OCR 与校对管线。',
            style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          ),
          const SizedBox(height: 6),
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
            action: ElevatedButton.icon(
              icon: const Icon(Icons.folder_open),
              label: const Text('选择文件'),
              onPressed: _busy ? null : _pickPdf,
            ),
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton.icon(
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
                    : (_busy ? '处理中…' : (hasFailedJobs ? '重试失败页面' : '开始处理')),
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
              style: ElevatedButton.styleFrom(
                minimumSize: const Size.fromHeight(48),
                backgroundColor: _processing ? Colors.red.shade700 : null,
                foregroundColor: _processing ? Colors.white : null,
              ),
            ),
          ),
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
              _projectStorePath ?? ScribeForgeCli.defaultStorePath(_pdfPath!),
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
          if (_projectStatus != null) ...[
            const SizedBox(height: 16),
            _buildProjectStatusCard(),
          ],
        ],
      ),
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

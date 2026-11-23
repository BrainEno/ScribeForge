part of 'unified_workspace.dart';

/// Settings actions and view composition, separate from the workspace shell.
extension _UnifiedWorkspaceSettings on _UnifiedWorkspaceState {
  // ========================================================= 设置页面

  /// First-run disk budget for the settings card (issue #214); null when no
  /// preflight has been reported yet.
  String? _diskBudgetLine() {
    final disk = _runtimeStatus?.disk;
    if (disk == null) return null;
    final required = formatBytes(disk.requiredBytes);
    final free = formatBytes(disk.freeBytes);
    return disk.sufficient
        ? '磁盘预算（预估）：首跑约需 $required，当前盘剩余 $free。'
        : '磁盘预算（预估）：首跑约需 $required，当前盘剩余 $free，还差约 '
              '${formatBytes(disk.missingBytes)}。';
  }

  bool get _diskInsufficient =>
      _runtimeStatus?.disk != null && !_runtimeStatus!.disk!.sufficient;

  String get _diskAdvice => _runtimeStatus?.disk?.advice ?? '';

  /// Runs `runtime-clean` and reports the freed space (issue #214).
  Future<void> _cleanRuntimeArtifacts() async {
    final root = _runtimeStatus?.runtimeRoot;
    if (root == null || root.isEmpty) {
      _updateWorkspace(() => _error = '尚未检测到运行时根目录，请先刷新运行时状态。');
      return;
    }
    _updateWorkspace(() {
      _runtimeCleaning = true;
      _error = null;
    });
    var output = '';
    try {
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.runtimeCleanArguments(runtimeRoot: root),
        onOutput: (chunk) => output += chunk,
        onError: (chunk) => output += chunk,
      );
      if (!mounted) return;
      if (exitCode != 0) {
        _updateWorkspace(() {
          _runtimeCleaning = false;
          _error = '清理失败（退出码 $exitCode）：${output.trim()}';
        });
        return;
      }
      var freed = 0;
      try {
        final decoded = jsonDecode(output);
        if (decoded is Map && decoded['freed_bytes'] is num) {
          freed = (decoded['freed_bytes'] as num).toInt();
        }
      } on FormatException {
        // Non-JSON output: still a success exit; show raw text.
      }
      _updateWorkspace(() {
        _runtimeCleaning = false;
        _notice = '已清理运行时临时文件，释放约 ${formatBytes(freed)}。';
      });
      await _refreshRuntimeStatus();
    } on Object catch (error) {
      if (!mounted) return;
      _updateWorkspace(() {
        _runtimeCleaning = false;
        _error = '清理失败：$error';
      });
    }
  }

  /// Engines that will take part in processing, read from runtime-status
  /// (issue #215): ready core engines first, then installed optional routes.
  String _engineComboSummary() {
    final status = _runtimeStatus;
    if (status == null) return '当前引擎组合：尚未检测运行时状态。';
    final ready = <String>[
      for (final name in status.backendNames)
        if (status.backends[name]?.state == 'ready' &&
            status.backends[name]?.environmentExists == true)
          name,
    ];
    if (ready.isEmpty) return '当前引擎组合：尚无就绪引擎，请先初始化环境。';
    final labels = ready
        .map(
          (name) => switch (name) {
            'mineru' => 'MinerU',
            'paddleocr' => 'PaddleOCR',
            'paddleocr-vl' => 'PaddleOCR-VL',
            'surya' => 'Surya',
            _ => name,
          },
        )
        .join(' + ');
    final optional = ready.where(
      (name) => status.backends[name]?.optional == true,
    );
    final suffix = optional.isEmpty ? '' : '（含可选引擎 ${optional.join(', ')}）';
    return '当前引擎组合：$labels$suffix';
  }

  String _backendDisplayLabel(String name) => switch (name) {
    'mineru' => 'MinerU',
    'paddleocr' => 'PaddleOCR',
    'paddleocr-vl' => 'PaddleOCR-VL',
    'surya' => 'Surya',
    _ => name,
  };

  /// Per-backend size estimate suffix for the settings list (issue #214).
  String _backendEstimateSuffix(RuntimeBackendSnapshot backend) {
    final estimate = backend.estimates;
    if (estimate == null || estimate.totalBytes <= 0) return '';
    return ' · 预估 ${formatBytes(estimate.totalBytes)}';
  }

  String get _environmentHeadline {
    if (_runtimeLoading || !_runtimeChecked) return '正在检测环境';
    if (!_backendConnected) return '处理核心不可用';
    if (_runtimeStatus == null) return '环境检测失败，状态未知';
    if (_runtimeHealthReport != null &&
        !_runtimeHealthReport!.success &&
        !_runtimeHealthReport!.cancelled) {
      return '示例检查未通过：请检查或修复环境';
    }
    if (_ocrRuntimeReady) return '基本就绪：可开始处理';
    return '尚未就绪：需要配置 OCR 环境';
  }

  String get _environmentExplanation {
    if (_runtimeLoading || !_runtimeChecked) return '正在检查处理核心、OCR 引擎与磁盘，请稍候。';
    if (!_backendConnected) return '无法启动处理核心。请检查完整安装包或在设置中修复核心。';
    if (_runtimeStatus == null) return '无法读取运行状态；请重新检测并查看错误详情。';
    if (_ocrRuntimeReady) {
      return 'MinerU 和 PaddleOCR 上次配置状态均为就绪且环境目录存在；这不是本次真实识别测试。';
    }
    return '处理需要 MinerU 和 PaddleOCR 两个核心引擎；可在设置中查看缺失项并配置。';
  }

  Future<void> _openRuntimeHealthCheck() async {
    if (_busy ||
        _runtimeLoading ||
        !_backendConnected ||
        _runtimeStatus == null)
      return;
    final result = await showDialog<RuntimeHealthReport>(
      context: context,
      barrierDismissible: false,
      builder: (_) => RuntimeHealthDialog(
        executor: _cliExecutor,
        executable: _cliExecutable,
      ),
    );
    if (!mounted || result == null) return;
    _updateWorkspace(() => _runtimeHealthReport = result);
  }

  String get _healthSummary {
    final report = _runtimeHealthReport;
    if (report == null) return '尚未进行本次示例识别检查。';
    if (report.cancelled) return '本次示例检查已取消，不代表环境可用。';
    if (report.success) return '本次两个引擎的示例识别通过；不保证书籍内容识别准确。';
    return '本次示例检查未全部通过，请到设置查看或重新检查。';
  }

  Widget _buildEnvironmentOverview() {
    return Card(
      key: const ValueKey('home-environment-overview'),
      margin: EdgeInsets.zero,
      color: _ocrRuntimeReady ? Colors.green.shade50 : Colors.orange.shade50,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              _environmentHeadline,
              key: const ValueKey('environment-headline'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 6),
            Text(_environmentExplanation),
            const SizedBox(height: 4),
            Text(_healthSummary, key: const ValueKey('home-health-summary')),
            if (_runtimeStatusError != null) ...[
              const SizedBox(height: 6),
              SelectableText(_runtimeStatusError!),
            ],
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                OutlinedButton.icon(
                  key: const ValueKey('home-environment-settings'),
                  onPressed: () => _updateWorkspace(() => _selectedIndex = 6),
                  icon: const Icon(Icons.settings_outlined),
                  label: const Text('查看环境与配置'),
                ),
                TextButton.icon(
                  key: const ValueKey('home-runtime-health'),
                  onPressed:
                      _backendConnected &&
                          !_runtimeLoading &&
                          !_busy &&
                          _runtimeStatus != null
                      ? _openRuntimeHealthCheck
                      : null,
                  icon: const Icon(Icons.fact_check_outlined),
                  label: const Text('测试 OCR 环境'),
                ),
                TextButton.icon(
                  onPressed: _runtimeLoading || _busy
                      ? null
                      : () => unawaited(_refreshRuntimeStatus()),
                  icon: const Icon(Icons.refresh),
                  label: const Text('重新检测环境'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _backendStateLabel(RuntimeBackendSnapshot? backend) {
    if (backend == null) return '未报告';
    if (backend.state == 'ready' && !backend.environmentExists) {
      return '状态为就绪，但环境目录缺失';
    }
    return switch (backend.state) {
      'ready' => '已就绪',
      'missing' => '未安装',
      'failed' => '配置失败',
      'installing' => '配置中或上次配置未完成',
      _ => '状态未知（${backend.state}）',
    };
  }

  Widget _buildEnvironmentDetails() {
    final status = _runtimeStatus;
    if (status == null) return const Text('尚无检测结果；请先重新检测。');
    final hardware = status.hardware;
    final gpu = hardware['gpu_name'];
    final ram = hardware['ram_mb'];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(_healthSummary, key: const ValueKey('settings-health-summary')),
        TextButton.icon(
          key: const ValueKey('settings-runtime-health'),
          onPressed: _backendConnected && !_runtimeLoading && !_busy
              ? _openRuntimeHealthCheck
              : null,
          icon: const Icon(Icons.fact_check_outlined),
          label: const Text('运行内置示例检查'),
        ),
        Text(
          '系统：${hardware['os'] ?? '未知'} · 架构：${hardware['architecture'] ?? '未知'}',
        ),
        Text(
          'NVIDIA GPU：${gpu is String && gpu.isNotEmpty ? gpu : '未检测到（不代表检测了所有 GPU）'}',
        ),
        Text(
          '内存：${ram is num ? '${(ram / 1024).toStringAsFixed(1)} GB' : '未知'}',
        ),
        SelectableText(
          '运行目录：${status.runtimeRoot.isEmpty ? '未知' : status.runtimeRoot}',
        ),
        const SizedBox(height: 8),
        for (final name in <String>[
          'mineru',
          'paddleocr',
          ...status.optionalBackendNames,
        ]) ...[
          Text(
            '${_backendDisplayLabel(name)}：${_backendStateLabel(status.backends[name])}'
            '${status.backends[name]?.optional == true ? '（可选，不影响基本处理）' : '（基本处理必需）'}',
            key: ValueKey('environment-detail-$name'),
          ),
          if (status.backends[name]?.failedStep != null)
            Text('上次失败步骤：${status.backends[name]!.failedStep}'),
          if (status.backends[name]?.estimates case final estimate?)
            Text(
              '完整安装预估：网络下载 ${formatBytes(estimate.downloadBytes)}；'
              '环境占用 ${formatBytes(estimate.installBytes)}；模型占用 ${formatBytes(estimate.modelBytes)}。'
              '${estimate.note.isEmpty ? '' : ' ${estimate.note}'}',
            ),
        ],
        const SizedBox(height: 8),
        const Text(
          '下载量是完整安装的粗略估计（包含模型），不是还需下载的精确大小；'
          '缓存、版本、CPU/GPU 与网络会改变实际用量。当前无法判断可升级版本或增量大小。'
          '可选引擎仅在明确选择时安装。',
        ),
        if (status.disk == null) const Text('磁盘预估不可用，请确认有足够空闲空间。'),
      ],
    );
  }

  Widget _buildInitializationCard() {
    final origin = _backendResolution?.origin;
    final coreLabel = switch (origin) {
      DesktopBackendOrigin.bundled => '内置处理核心（无需系统 Python）',
      DesktopBackendOrigin.provisionedVenv => '已初始化的本地后端环境（backend-venv）',
      DesktopBackendOrigin.custom => '自定义处理核心（开发者配置）',
      DesktopBackendOrigin.developmentPath => 'PATH 开发模式（需要已安装 CLI）',
      null => '正在检测处理核心',
    };
    return Card(
      key: const ValueKey('runtime-initialization-card'),
      margin: EdgeInsets.zero,
      color: _ocrRuntimeReady ? Colors.green.shade50 : null,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(
                  _ocrRuntimeReady
                      ? Icons.check_circle_outline
                      : Icons.download_for_offline_outlined,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    _environmentHeadline,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(_environmentExplanation),
            if (_runtimeStatusError != null)
              SelectableText(_runtimeStatusError!),
            Text('核心来源：$coreLabel'),
            const SizedBox(height: 6),
            Text(
              _backendConnected
                  ? '点击初始化后，会自动准备 uv、私有 Python 3.12、MinerU、PaddleOCR 和模型。不会修改系统 Python；首次安装需要网络与额外磁盘空间。'
                  : '处理核心未连接。完整桌面安装包会附带该组件；请重新检测，或重新安装完整桌面版。开发者仍可在下方指定 CLI。',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            if (_diskBudgetLine() != null) ...<Widget>[
              const SizedBox(height: 6),
              Text(
                _diskBudgetLine()!,
                key: const ValueKey('disk-budget-line'),
                style: TextStyle(
                  color: _diskInsufficient
                      ? Colors.red.shade700
                      : Colors.grey.shade700,
                  fontSize: 12,
                ),
              ),
            ],
            if (_diskInsufficient && _diskAdvice.isNotEmpty) ...<Widget>[
              const SizedBox(height: 4),
              Text(
                _diskAdvice,
                key: const ValueKey('disk-insufficient-advice'),
                style: TextStyle(color: Colors.red.shade700, fontSize: 12),
              ),
            ],
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                FilledButton.icon(
                  key: const ValueKey('initialize-runtime-button'),
                  // Issue #214: never start the first run when the runtime
                  // root drive cannot fit the estimated footprint.
                  onPressed:
                      _backendConnected &&
                          !_runtimeLoading &&
                          !_busy &&
                          !_diskInsufficient
                      ? _showInitialization
                      : null,
                  icon: const Icon(Icons.download),
                  label: Text(_ocrRuntimeReady ? '检查或修复环境' : '初始化环境'),
                ),
                OutlinedButton.icon(
                  onPressed: _runtimeLoading
                      ? null
                      : () => unawaited(_refreshRuntimeStatus()),
                  icon: const Icon(Icons.refresh),
                  label: const Text('重新检测'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _openRuntimeLocations() async {
    if (_busy || _runtimeLoading || _runtimeCleaning) return;
    final before = _runtimeLocations;
    final changed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => RuntimeLocationDialog(
        preferences: before,
        activeRuntimeRoot: _runtimeStatus?.runtimeRoot,
        filePicker: _filePicker,
        service: RuntimeLocationService(_rawCliExecutor, _cliExecutable),
        activate: (location) async {
          final result = await _settingsStore.activateRuntimeLocation(
            expected: before.active,
            next: location,
          );
          if (mounted && result.ok) {
            _updateWorkspace(
              () => _runtimeLocations = before.activate(location),
            );
          }
          return result;
        },
      ),
    );
    if (!mounted || changed != true) return;
    await _refreshRuntimeStatus();
    if (!mounted || !_ocrRuntimeReady) return;
    _updateWorkspace(() {
      _notice = '已切换运行环境，可以导入或继续处理书籍。';
      _selectedIndex = _pdfPath == null ? 0 : 1;
    });
  }

  Widget _buildRuntimeLocationTile() {
    final locations = _runtimeLocations;
    final active = locations.active;
    return ListTile(
      key: const ValueKey('runtime-location-settings'),
      leading: const Icon(Icons.folder_copy_outlined),
      title: const Text('运行环境与模型位置'),
      subtitle: Text(
        active.isDefault
            ? '当前使用默认位置。空间不足时可选另一块磁盘，或复用已有模型。'
            : '运行目录：${active.runtimeRoot.isEmpty ? "系统默认" : active.runtimeRoot}\n'
                  '模型目录：${active.usesExistingModels ? active.modelsRoot : "由运行环境管理"}',
      ),
      trailing: const Icon(Icons.chevron_right),
      onTap: _busy || _runtimeLoading || _runtimeCleaning
          ? null
          : _openRuntimeLocations,
    );
  }

  Widget _buildSettingsPage() {
    return ScribePageBody(
      children: <Widget>[
        const ScribePageHeader(
          icon: Icons.settings_outlined,
          title: '设置',
          subtitle: '配置本机的处理核心、OCR 模型和复核选项。桌面安装包无需预装 Python。',
        ),
        const SizedBox(height: ScribeSpacing.x5),
        if (_settingsCorruption != null) ...<Widget>[
          _buildCorruptionCard(),
          const SizedBox(height: 12),
        ],
        _buildInitializationCard(),
        const SizedBox(height: 12),
        ScribeSectionCard(
          icon: Icons.fact_check_outlined,
          title: '检测结果与下载说明',
          child: _buildEnvironmentDetails(),
        ),
        const SizedBox(height: 12),
        _buildBackendEnvironmentCard(),
        const SizedBox(height: 12),
        ListTile(
          title: const Text('命令行工具'),
          subtitle: Text(
            '$_cliExecutable\n来源：$_backendOriginLabel'
            '${_cliProbeMessage == null ? '' : '\n$_cliProbeMessage '}',
          ),
          trailing: IconButton(
            icon: const Icon(Icons.edit),
            onPressed: _busy || _cliProbing ? null : _editCliExecutable,
          ),
        ),
        ListTile(
          title: const Text('视觉模型复核'),
          subtitle: Text(
            _vlmSettings.enabled
                ? '已启用：处理时会按配置发送复核裁切图。'
                : '默认关闭；未明确启用时不会发送复核裁切图。',
          ),
          trailing: TextButton(
            onPressed: _busy ? null : _openVlmSettings,
            child: const Text('配置 VLM 复核'),
          ),
        ),
        const Divider(),
        ListTile(
          title: const Text('运行时配置 (setup)'),
          subtitle: const Text('查看后端、阶段与步骤进度，或修复单个后端'),
          trailing: _runtimeLoading
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.play_arrow),
          onTap: _runtimeLoading ? null : _openRuntimeSetup,
        ),
        _buildRuntimeLocationTile(),
        if (_runtimeStatus != null) ...[
          const Divider(),
          const Text('运行时状态', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('根目录：${_runtimeStatus!.runtimeRoot}'),
          Text('存在：${_runtimeStatus!.runtimeRootExists}'),
          Text('uv 可用：${_runtimeStatus!.uvAvailable}'),
          if (_runtimeStatus!.disk != null) ...[
            const SizedBox(height: 4),
            Text(
              '磁盘：${_runtimeStatus!.disk!.path} 剩余 '
              '${formatBytes(_runtimeStatus!.disk!.freeBytes)} / 共 '
              '${formatBytes(_runtimeStatus!.disk!.totalBytes)}'
              '（首跑预估需要 ${formatBytes(_runtimeStatus!.disk!.requiredBytes)}）',
              key: const ValueKey('runtime-disk-line'),
              style: TextStyle(
                fontSize: 12,
                color: _runtimeStatus!.disk!.sufficient
                    ? Colors.grey.shade600
                    : Colors.red.shade700,
              ),
            ),
          ],
          const SizedBox(height: 8),
          Text('后端：', style: const TextStyle(fontWeight: FontWeight.w600)),
          ..._runtimeStatus!.backends.entries.map(
            (entry) => Padding(
              padding: const EdgeInsets.only(left: 16, top: 4),
              child: Text(
                '${_backendDisplayLabel(entry.key)}: ${_backendStateLabel(entry.value)} '
                '${entry.value.environmentExists ? '' : '(环境未安装)'}'
                '${entry.value.optional ? ' · 可选引擎' : ''}'
                '${_backendEstimateSuffix(entry.value)}',
                key: ValueKey('runtime-backend-${entry.key}'),
                style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
              ),
            ),
          ),
        ],
        const Divider(),
        ListTile(
          title: const Text('刷新运行时状态'),
          trailing: const Icon(Icons.refresh),
          onTap: () {
            _refreshRuntimeStatus();
          },
        ),
        // Issue #214: actionable cleanup when the disk preflight is short.
        ListTile(
          title: const Text('清理运行时临时文件'),
          subtitle: const Text('删除未完成的临时环境目录；模型与缓存默认保留'),
          trailing: _runtimeCleaning
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.delete_sweep_outlined),
          onTap: _runtimeLoading || _runtimeCleaning
              ? null
              : _cleanRuntimeArtifacts,
        ),
      ],
    );
  }

  // ============================================================ 后端环境

  String get _backendVenvDirectory =>
      _provisioner.defaultVenvDirectory(_settingsStore.settingsDirectory.path);

  Widget _buildBackendEnvironmentCard() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              '后端环境（Python 虚拟环境）',
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 4),
            Text(
              '无需手动执行命令行：直接点击'
              '“自动创建后端环境”，客户端会检测 Python 和源码目录，安装后端并接着配置 OCR。'
              '完整桌面安装包自带后端，无需此步骤。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
            ),
            const SizedBox(height: 8),
            Text(
              'Python 解释器：${_backendPython.isEmpty ? '未设置（将自动检测系统 Python）' : _backendPython}',
              style: const TextStyle(fontSize: 12),
            ),
            Text(
              '源码目录：${_backendSourcePath.isEmpty ? '未设置（将自动向上检测）' : _backendSourcePath}',
              style: const TextStyle(fontSize: 12),
            ),
            Text(
              '虚拟环境目录：$_backendVenvDirectory',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                OutlinedButton.icon(
                  onPressed: _backendBusy ? null : _pickBackendPython,
                  icon: const Icon(Icons.edit),
                  label: const Text('选择 Python 解释器'),
                ),
                OutlinedButton.icon(
                  onPressed: _backendBusy ? null : _pickBackendSourcePath,
                  icon: const Icon(Icons.folder_open),
                  label: const Text('选择源码目录'),
                ),
                FilledButton.icon(
                  key: const ValueKey('provision-backend-button'),
                  onPressed: _backendBusy || _busy
                      ? null
                      : () => unawaited(_provisionBackendEnvironment()),
                  icon: const Icon(Icons.build),
                  label: const Text('自动创建后端环境'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _pickBackendPython() async {
    final value = await _promptString(
      'Python 解释器路径',
      _backendPython.isEmpty ? 'python3' : _backendPython,
    );
    if (!mounted || value == null || value.trim().isEmpty) return;
    final candidate = value.trim();
    // Validate before persisting (issue #221): a Python path that cannot
    // even answer `--version` is rejected and the old value stays in effect.
    _updateWorkspace(() {
      _cliProbing = true;
      _cliProbeMessage = '正在检测 Python：$candidate';
      _error = null;
    });
    final probe = await const CliPathValidator(
      probeArguments: <String>['--version'],
    ).probe(candidate);
    if (!mounted) return;
    if (!probe.ok) {
      _updateWorkspace(() {
        _cliProbing = false;
        _cliProbeMessage = null;
        _error = '未保存 Python 路径（旧配置保持不变）。${probe.hint ?? ''}';
      });
      return;
    }
    final result = await _settingsStore.saveBackendPython(candidate);
    if (!mounted) return;
    if (!result.ok) {
      _updateWorkspace(() {
        _cliProbing = false;
        _cliProbeMessage = null;
        _error = '保存 Python 路径失败：${result.error}。输入已保留，可重试。';
      });
      return;
    }
    _updateWorkspace(() {
      _cliProbing = false;
      _backendPython = candidate;
      _cliProbeMessage = 'Python 检测通过：${probe.output.split('\n').first}';
      _error = null;
    });
  }

  Future<void> _pickBackendSourcePath() async {
    final picked = await _filePicker.getDirectoryPath(
      dialogTitle: '选择 ScribeForge 源码仓库目录（包含 pyproject.toml）',
    );
    if (picked == null || picked.trim().isEmpty) return;
    final result = await _settingsStore.saveBackendSourcePath(picked.trim());
    if (!mounted) return;
    if (!result.ok) {
      _updateWorkspace(() => _error = '保存源码目录失败：${result.error}。输入已保留，可重试。');
      return;
    }
    _updateWorkspace(() {
      _backendSourcePath = picked.trim();
      _error = null;
    });
  }

  Future<void> _provisionBackendEnvironment() async {
    if (_backendBusy) return;
    _updateWorkspace(() {
      _backendBusy = true;
      _error = null;
    });
    var connected = false;
    try {
      connected = await _createBackendEnvironment();
    } on Object catch (error) {
      if (mounted) _updateWorkspace(() => _error = '无法配置 Python 后端：$error');
    } finally {
      if (mounted) _updateWorkspace(() => _backendBusy = false);
    }
    if (!mounted || !connected) return;
    if (_ocrRuntimeReady) {
      _continueAfterRuntimeSetup();
    } else {
      await _openRuntimeSetup();
    }
  }

  Future<bool> _createBackendEnvironment() async {
    var python = _backendPython.trim();
    var pythonArgs = <String>[];
    if (python.isEmpty) {
      // #184: never ask a novice for a path — probe the system first.
      final detected = await _pythonLocator.detect();
      if (!mounted) return false;
      if (detected == null) {
        await showDialog<void>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('未检测到 Python 3'),
            content: const SingleChildScrollView(
              child: Text(
                '已自动尝试 py -3、python3、python 以及常见安装路径，均未找到可用的 Python 3。\n\n'
                '请安装 Python 3.10 或更高版本后重试：\n'
                '• Windows：winget install Python.Python.3.12，或从 python.org 安装并勾选 '
                '“Add python.exe to PATH”\n'
                '• macOS：brew install python@3.12\n'
                '• Linux：sudo apt install python3 python3-venv\n\n'
                '也可以点击“选择 Python 解释器”手动指定已有安装的路径（高级用户）。',
              ),
            ),
            actions: <Widget>[
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('知道了'),
              ),
            ],
          ),
        );
        return false;
      }
      python = detected.executable;
      pythonArgs = detected.arguments;
      _updateWorkspace(() => _notice = '自动检测到系统 Python：${detected.label}');
    }
    var sourcePath = _backendSourcePath.trim();
    if (sourcePath.isEmpty) {
      final detected = await _provisioner.detectBackendSourcePath();
      if (detected != null) {
        sourcePath = detected;
      } else {
        if (!mounted) return false;
        final picked = await _filePicker.getDirectoryPath(
          dialogTitle: '选择 ScribeForge 源码仓库目录（包含 pyproject.toml）',
        );
        if (!mounted || picked == null || picked.trim().isEmpty) return false;
        sourcePath = picked.trim();
      }
    }
    if (!mounted) return false;
    final result = await _provisioner.provision(
      pythonExecutable: python,
      pythonArgs: pythonArgs,
      backendSourcePath: sourcePath,
      venvDirectory: _backendVenvDirectory,
    );
    if (!mounted) return false;
    if (!result.ok) {
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('后端环境创建失败'),
          content: SingleChildScrollView(
            child: SelectableText('${result.error ?? '未知错误'}\n\n${result.log}'),
          ),
          actions: <Widget>[
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('关闭'),
            ),
          ],
        ),
      );
      return false;
    }

    // Keep the detected launcher automatic: saving just `py` would lose
    // its `-3` argument on the next rebuild.
    final sourceSave = await _settingsStore.saveBackendSourcePath(sourcePath);
    final cliSave = await _settingsStore.saveCliExecutable(
      result.cliExecutable,
    );
    final resolution = await _backendResolver.resolve(result.cliExecutable);
    if (!mounted) return false;
    _updateWorkspace(() {
      _backendSourcePath = sourcePath;
      _cliExecutable = resolution.executable;
      _backendResolution = resolution;
      _notice = 'Python 后端已就绪，正在检查 OCR 环境。';
    });
    await _refreshRuntimeStatus();
    await _refreshStatus();
    await _refreshReviewQueue();
    if (!mounted) return false;
    if (!sourceSave.ok || !cliSave.ok) {
      final failed = !sourceSave.ok ? sourceSave.error : cliSave.error;
      _updateWorkspace(() => _error = '后端已连接，但保存路径失败：$failed。');
    }
    return _backendConnected;
  }

  /// 简单的文本输入对话框。
  String get _backendOriginLabel => switch (_backendResolution?.origin) {
    DesktopBackendOrigin.bundled => '内置核心',
    DesktopBackendOrigin.provisionedVenv => '已初始化的本地环境',
    DesktopBackendOrigin.custom => '自定义路径',
    DesktopBackendOrigin.developmentPath => 'PATH 开发模式',
    null => '未检测',
  };

  Widget _buildCorruptionCard() {
    final corruption = _settingsCorruption!;
    return Card(
      color: Colors.amber.shade50,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(Icons.warning_amber_rounded, color: Colors.amber.shade800),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    corruption.message,
                    style: TextStyle(
                      color: Colors.amber.shade900,
                      fontSize: 13,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '${corruption.backedUp ? '备份文件' : '原文件'}：${corruption.backupPath}',
                    style: TextStyle(color: Colors.grey.shade700, fontSize: 12),
                  ),
                ],
              ),
            ),
            if (corruption.backedUp)
              TextButton(
                onPressed: () {
                  _settingsStore.clearPendingCorruption();
                  _updateWorkspace(() => _settingsCorruption = null);
                },
                child: const Text('知道了'),
              ),
          ],
        ),
      ),
    );
  }

  /// Prompts for a custom CLI path and validates it *before* persisting
  /// (issue #221): an invalid path is never saved and the old configured
  /// value stays in effect.
  Future<void> _editCliExecutable() async {
    final value = await _promptString('命令行工具路径', _cliExecutable);
    if (!mounted || value == null || value.trim().isEmpty) return;
    final candidate = value.trim();
    _updateWorkspace(() {
      _cliProbing = true;
      _cliProbeMessage = '正在检测：$candidate';
      _error = null;
    });
    final probe = await _cliPathValidator.probe(candidate);
    if (!mounted) return;
    if (!probe.ok) {
      _updateWorkspace(() {
        _cliProbing = false;
        _cliProbeMessage = '无法运行：${probe.status.name}';
        _error = '未保存命令行工具路径（旧配置保持不变）。${probe.hint ?? ''}';
      });
      return;
    }
    final saved = await _settingsStore.saveCliExecutable(candidate);
    if (!mounted) return;
    if (!saved.ok) {
      _updateWorkspace(() {
        _cliProbing = false;
        _cliProbeMessage = null;
        _error = '检测通过但保存失败：${saved.error}。输入已保留，可重试。';
      });
      return;
    }
    final venvCliPath = BackendVenvPaths(_backendVenvDirectory).cliExecutable;
    final resolution = await _backendResolver.resolve(
      candidate,
      venvCliPath: venvCliPath,
    );
    if (!mounted) return;
    _updateWorkspace(() {
      _cliProbing = false;
      _cliExecutable = resolution.executable;
      _backendResolution = resolution;
      _cliProbeMessage = probe.hint;
    });
    await _refreshRuntimeStatus();
    await _refreshStatus();
    await _refreshReviewQueue();
  }
}

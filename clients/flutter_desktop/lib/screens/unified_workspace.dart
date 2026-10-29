import 'dart:convert';

import 'package:flutter/material.dart';

import '../models/project_status.dart';
import '../models/runtime_status.dart';
import '../services/cli_runner.dart';
import '../services/settings_store.dart';

/// 一体化书籍数字化工作台。
///
/// 面向新手使用者：把「导入书籍 → 运行处理 → 复核决策 → 导出 EPUB/DOCX」
/// 全流程聚合在一个窗口里，无需接触命令行。首次启动会引导完成运行时
/// 配置（setup），之后所有操作都通过界面按钮完成。
///
/// 与 Python CLI 保持一致的语义：
/// - `process`：导入并运行整条管线（OCR / 对齐 / 风险 / 复核 / 决策）
/// - `review`：列出尚需人工决定的配对
/// - `resolve`：记录一条人工决策
/// - `confirm`：标记空白页 / 非文本页
/// - `export`：写出 EPUB 3 或 DOCX
/// - `runtime-status` / `setup`：运行时与首次配置
class UnifiedWorkspace extends StatefulWidget {
  const UnifiedWorkspace({super.key});

  @override
  State<UnifiedWorkspace> createState() => _UnifiedWorkspaceState();
}

class _UnifiedWorkspaceState extends State<UnifiedWorkspace> {
  int _selectedIndex = 0;

  String? _pdfPath;
  String? _projectStorePath;
  late final TextEditingController _pdfPathController;
  bool _projectSourceMatches = true;
  String? _projectSourceMismatchMessage;

  final _settingsStore = DesktopSettingsStore();
  String _cliExecutable = ScribeForgeCli.defaultExecutable;
  bool _busy = false;
  String? _notice;
  String? _error;

  ProjectStatusSnapshot? _projectStatus;
  int _statusRequestVersion = 0;

  RuntimeStatusSnapshot? _runtimeStatus;
  bool _runtimeLoading = false;

  // 复核队列（从 `scribeforge review` 解析）
  final List<ReviewQueueItem> _reviewQueue = [];
  bool _reviewLoading = false;

  // 导出表单控制器（需在 dispose 中释放）
  final _exportOutputController = TextEditingController();
  final _exportLanguageController = TextEditingController(text: 'zh');
  final _exportTitleController = TextEditingController();
  final _exportAuthorController = TextEditingController();
  var _exportFormatEpub = true;
  var _exportLayoutChapter = true;
  var _exportStrictAcceptance = false;

  // 复核队列每条项的输入控制器（键：pageIndex-pairIndex）
  final Map<String, TextEditingController> _reviewControllers = {};

  TextEditingController _reviewControllerFor(ReviewQueueItem item) {
    final key = '${item.pageIndex}-${item.pairIndex}';
    return _reviewControllers.putIfAbsent(key, () => TextEditingController());
  }

  static const List<String> _navTitles = <String>[
    '书籍',
    '处理',
    '复核',
    '导出',
    '设置',
  ];

  static const List<IconData> _navIcons = <IconData>[
    Icons.library_books,
    Icons.auto_awesome,
    Icons.reviews,
    Icons.output,
    Icons.settings,
  ];

  @override
  void initState() {
    super.initState();
    _pdfPathController = TextEditingController(text: _pdfPath ?? '');
    _loadSettings();
    _refreshStatus();
    _refreshRuntimeStatus();
  }

  @override
  void dispose() {
    _pdfPathController.dispose();
    _exportOutputController.dispose();
    _exportLanguageController.dispose();
    _exportTitleController.dispose();
    _exportAuthorController.dispose();
    for (final controller in _reviewControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _loadSettings() async {
    final executable = await _settingsStore.loadCliExecutable();
    if (!mounted) return;
    setState(() => _cliExecutable = executable);
  }

  // ----------------------------------------------------------------- status

  Future<void> _refreshStatus() async {
    if (_projectStorePath == null) return;
    final version = ++_statusRequestVersion;
    try {
      final arguments = ScribeForgeCli.statusArguments(
        storePath: _projectStorePath!,
      );
      final buffer = StringBuffer();
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted || version != _statusRequestVersion) return;
      if (exitCode == 0) {
        setState(() {
          _projectStatus = ProjectStatusSnapshot.fromJsonText(
                buffer.toString(),
                sourcePath: _pdfPath!,
              );
          _error = null;
        });
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted || version != _statusRequestVersion) return;
      setState(() => _error = '读取项目状态失败：$exc');
    } finally {
      if (mounted && version == _statusRequestVersion) {
        setState(() {});
      }
    }
  }

  // ---------------------------------------------------------------- runtime

  Future<void> _refreshRuntimeStatus() async {
    setState(() => _runtimeLoading = true);
    try {
      final arguments = ScribeForgeCli.runtimeStatusArguments();
      final buffer = StringBuffer();
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0) {
        setState(() {
          _runtimeStatus = RuntimeStatusSnapshot.fromJsonText(buffer.toString());
          _error = null;
        });
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '读取运行时状态失败：$exc');
    } finally {
      if (mounted) {
        setState(() => _runtimeLoading = false);
      }
    }
  }

  // ------------------------------------------------------------- review queue

  Future<void> _refreshReviewQueue() async {
    if (_projectStorePath == null) return;
    setState(() => _reviewLoading = true);
    _reviewQueue.clear();
    try {
      final arguments = ScribeForgeCli.reviewArguments(storePath: _projectStorePath!);
      final buffer = StringBuffer();
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      setState(() {
        _reviewQueue.addAll(_parseReviewText(buffer.toString()));
        if (exitCode != 0) {
          _error = buffer.toString().trim();
        }
      });
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '读取复核队列失败：$exc');
    } finally {
      if (mounted) {
        setState(() => _reviewLoading = false);
      }
    }
  }

  /// 把 `scribeforge review` 的文本输出解析成队列项。
  ///
  /// 实际输出格式由 CLI 决定；这里做宽松的逐行解析，把类似
  /// `page 3 pair 1` 的行当作一条待处理项。
  List<ReviewQueueItem> _parseReviewText(String text) {
    final items = <ReviewQueueItem>[];
    final lines = LineSplitter.split(text);
    for (final line in lines) {
      final trimmed = line.trim();
      if (trimmed.isEmpty) continue;
      final pageMatch = RegExp(r'page\s+(\d+)', caseSensitive: false).firstMatch(trimmed);
      final pairMatch = RegExp(r'pair\s+(\d+)', caseSensitive: false).firstMatch(trimmed);
      if (pageMatch != null && pairMatch != null) {
        items.add(ReviewQueueItem(
          pageIndex: int.parse(pageMatch.group(1)!),
          pairIndex: int.parse(pairMatch.group(1)!),
          description: trimmed,
        ));
      } else if (trimmed.startsWith('-') || trimmed.startsWith('•')) {
        items.add(ReviewQueueItem(
          pageIndex: null,
          pairIndex: null,
          description: trimmed,
        ));
      }
    }
    return items;
  }

  // ---------------------------------------------------------------- actions

  Future<void> _runProcess() async {
    if (_pdfPath == null) return;
    final storePath = _projectStorePath ??
        ScribeForgeCli.defaultStorePath(_pdfPath!);
    setState(() {
      _busy = true;
      _projectStorePath = storePath;
      _error = null;
      _notice = null;
    });
    try {
      final arguments = ScribeForgeCli.processArguments(
        pdfPath: _pdfPath!,
        storePath: storePath,
      );
      final buffer = StringBuffer();
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable,
        arguments: arguments,
          onOutput: (chunk) {
            if (!mounted) return;
            final lines = chunk.trim().split('\n');
            if (lines.isNotEmpty) setState(() => _notice = lines.last);
          },
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0 || exitCode == 1) {
        // 退出码 1 表示部分页面失败，但书籍仍可继续；把状态刷新出来。
        await _detectSourceMatch(storePath);
        await _refreshStatus();
        await _refreshReviewQueue();
        setState(() {
          _notice = exitCode == 0
              ? '处理完成'
              : '处理完成（部分页面失败，见下方通知）';
        });
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '处理失败：$exc');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  /// 导入后判断已选 PDF 与项目里记录的源路径/摘要是否一致。
  Future<void> _detectSourceMatch(String storePath) async {
    try {
      final arguments = ScribeForgeCli.statusArguments(storePath: storePath);
      final buffer = StringBuffer();
      await ScribeForgeCli.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (_) {},
      );
      if (_projectStatus != null) {
        setState(() {
          _projectSourceMatches = _projectStatus!.sourcePathMatches(_pdfPath ?? '');
          if (!_projectSourceMatches) {
            _projectSourceMismatchMessage =
                '已选 PDF 与项目记录的源文件不一致，请确认使用同一本书。';
          }
        });
      }
    } catch (_) {
      // 状态检查失败不阻塞主流程。
    }
  }

  Future<void> _resolveReviewPair(ReviewQueueItem item, String acceptedText) async {
    if (item.pageIndex == null || item.pairIndex == null || _projectStorePath == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      final arguments = ScribeForgeCli.resolveArguments(
        storePath: _projectStorePath!,
        page: item.pageIndex!,
        pair: item.pairIndex!,
        text: acceptedText,
      );
      final buffer = StringBuffer();
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0) {
        setState(() {
          _reviewQueue.removeWhere((r) =>
              r.pageIndex == item.pageIndex && r.pairIndex == item.pairIndex);
          _notice = '已记录第 ${item.pageIndex! + 1} 页第 ${item.pairIndex!} 对的人工决策';
          _error = null;
        });
        await _refreshStatus();
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '记录决策失败：$exc');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _runExport({
    required String outputPath,
    required String language,
    required String title,
    String? author,
    String? modified,
    bool layoutChapter = true,
    bool strictAcceptance = false,
  }) async {
    if (_projectStorePath == null) return;
    setState(() {
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
      );
      // 布局与严格校验：CLI 支持 --layout 与 --strict-acceptance，这里拼接。
      final allArgs = <String>[...arguments];
      allArgs.addAll(<String>['--layout', layoutChapter ? 'chapter' : 'page']);
      if (strictAcceptance) allArgs.add('--strict-acceptance');

      final buffer = StringBuffer();
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable,
        arguments: allArgs,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0) {
        setState(() {
          _notice = '导出成功：$outputPath';
        });
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '导出失败：$exc');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _runSetup({String? runtimeRoot}) async {
    setState(() => _busy = true);
    try {
      final arguments = ScribeForgeCli.setupArguments(runtimeRoot: runtimeRoot);
      final buffer = StringBuffer();
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0) {
        setState(() {
          _notice = '运行时配置完成';
          _error = null;
        });
        await _refreshRuntimeStatus();
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '运行时配置失败：$exc');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  // ------------------------------------------------------------------ build

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_navTitles[_selectedIndex]),
        actions: <Widget>[
          if (_busy)
            Padding(
              padding: EdgeInsets.all(8),
              child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
            ),
          if (_error != null)
            IconButton(
              icon: const Icon(Icons.error_outline, color: Colors.amber),
              onPressed: () {
                setState(() => _error = null);
              },
            ),
        ],
      ),
      body: Column(
        children: <Widget>[
          _buildNoticeBar(),
          Expanded(
            child: Row(
              children: <Widget>[
                NavigationRail(
                  selectedIndex: _selectedIndex,
                  onDestinationSelected: (index) {
                    if (index != null) setState(() => _selectedIndex = index);
                  },
                  labelType: NavigationRailLabelType.all,
                  destinations: List.generate(
                    _navTitles.length,
                    (index) => NavigationRailDestination(
                      icon: Icon(_navIcons[index]),
                      selectedIcon: Icon(_navIcons[index], color: Theme.of(context).colorScheme.primary),
                      label: Text(_navTitles[index]),
                    ),
                  ),
                ),
                const VerticalDivider(thickness: 1, width: 1),
                Expanded(
                  child: IndexedStack(
                    index: _selectedIndex,
                    children: <Widget>[
                      _buildLibraryPage(),
                      _buildProcessPage(),
                      _buildReviewPage(),
                      _buildExportPage(),
                      _buildSettingsPage(),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildNoticeBar() {
    if (_notice == null && _error == null) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: _error == null ? Colors.green.shade50 : Colors.amber.shade50,
      child: Row(
        children: <Widget>[
          Icon(_error == null ? Icons.info : Icons.error_outline,
              color: _error == null ? Colors.green.shade700 : Colors.amber.shade700, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              _error ?? _notice!,
              style: TextStyle(color: _error == null ? Colors.green.shade800 : Colors.amber.shade800, fontSize: 12),
            ),
          ),
          if (_error != null)
            IconButton(icon: const Icon(Icons.close, size: 16), onPressed: () => setState(() => _error = null)),
        ],
      ),
    );
  }

  // ============================================================ 书籍页面

  Widget _buildLibraryPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('已导入的书籍', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('选择一本书查看处理状态与进度。尚未导入任何书籍，请前往“处理”页面添加 PDF。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 20),
          if (_projectStatus == null)
            const Text('暂无项目。', style: TextStyle(color: Colors.grey))
          else
            _buildProjectStatusCard(),
        ],
      ),
    );
  }

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
                Expanded(child: Text(status.title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600))),
                if (!_projectSourceMatches)
                  const Icon(Icons.warning, color: Colors.amber, size: 18),
              ],
            ),
            const SizedBox(height: 4),
            Text(status.sourceUri, style: TextStyle(color: Colors.grey.shade600, fontSize: 12)),
            const SizedBox(height: 12),
            LinearProgressIndicator(value: status.ocrProgress),
            const SizedBox(height: 6),
            Text(status.progressLabel, style: const TextStyle(fontSize: 12)),
            const SizedBox(height: 8),
            Text(status.jobsLabel, style: const TextStyle(fontSize: 12)),
            const SizedBox(height: 8),
            Text('复核队列：${status.reviewQueued} 项待处理', style: const TextStyle(fontSize: 12)),
            if (status.failedJobDetails.isNotEmpty) ...[
              const SizedBox(height: 8),
              const Text('失败任务：', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 12)),
              ...status.failedJobDetails.map((failed) => Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(failed.description, style: TextStyle(color: Colors.red.shade700, fontSize: 11)),
              )),
            ],
          ],
        ),
      ),
    );
  }

  // ========================================================= 处理页面

  Widget _buildProcessPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('导入并处理书籍', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('选择扫描书籍的 PDF 或页面图片，运行 OCR 与双引擎对齐管线。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 20),
          TextField(
            controller: _pdfPathController,
            decoration: const InputDecoration(
              labelText: '源文件 PDF 路径',
              hintText: '手动输入扫描书籍 PDF 的完整路径',
              prefixIcon: Icon(Icons.description),
            ),
            onChanged: (value) => setState(() => _pdfPath = value.trim()),
          ),
          const SizedBox(height: 16),
          Row(
            children: <Widget>[
              ElevatedButton.icon(
                icon: Icon(_busy ? Icons.hourglass_empty : Icons.play_arrow),
                label: Text(_busy ? '处理中…' : '开始处理'),
                onPressed: _busy || _pdfPath == null ? null : () { _runProcess(); },
                style: ElevatedButton.styleFrom(
                  minimumSize: const Size(double.infinity, 48),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          if (_pdfPath != null) ...[
            const Text('项目存储目录：', style: TextStyle(fontSize: 12, color: Colors.grey)),
            const SizedBox(height: 4),
            Text(_projectStorePath ?? ScribeForgeCli.defaultStorePath(_pdfPath!),
                style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
            const SizedBox(height: 12),
            if (!_projectSourceMatches && _projectSourceMismatchMessage != null)
              Text(_projectSourceMismatchMessage!,
                  style: TextStyle(color: Colors.amber.shade700, fontSize: 12)),
          ],
        ],
      ),
    );
  }

  // ========================================================= 复核页面

  Widget _buildReviewPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('复核队列', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('两个 OCR 引擎意见不一致的配对需要人工确认。选择一条，输入接受的文字，记录决策。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 16),
          Row(
            children: <Widget>[
              ElevatedButton.icon(
                icon: Icon(_reviewLoading ? Icons.hourglass_empty : Icons.refresh),
                label: Text(_reviewLoading ? '刷新中…' : '刷新队列'),
                onPressed: _reviewLoading ? null : () { _refreshReviewQueue(); },
              ),
              const SizedBox(width: 12),
              if (_projectStatus != null)
                Text('${_projectStatus!.reviewQueued} 项待处理',
                    style: const TextStyle(fontSize: 13)),
            ],
          ),
          const SizedBox(height: 12),
          if (_reviewLoading)
            const Center(child: CircularProgressIndicator(strokeWidth: 2))
          else if (_reviewQueue.isEmpty)
            const Text('暂无待处理配对。', style: TextStyle(color: Colors.grey))
          else
            Expanded(
              child: ListView.builder(
                itemCount: _reviewQueue.length,
                itemBuilder: (context, index) {
                  final item = _reviewQueue[index];
                  return _buildReviewQueueItem(item);
                },
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildReviewQueueItem(ReviewQueueItem item) {
    final controller = _reviewControllerFor(item);
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Text(
                  item.pageIndex == null || item.pairIndex == null
                      ? '未知页'
                      : '第 ${item.pageIndex! + 1} 页 · 第 ${item.pairIndex!} 对',
                  style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                ),
                Spacer(),
                if (_busy) const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
              ],
            ),
            const SizedBox(height: 4),
            Text(item.description, style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
            const SizedBox(height: 8),
            TextField(
              controller: controller,
              decoration: const InputDecoration(
                labelText: '接受的文字',
                hintText: '输入这行应该显示的文字',
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: <Widget>[
                ElevatedButton(
                  onPressed: item.pageIndex == null || item.pairIndex == null || _busy
                      ? null
                      : () { _resolveReviewPair(item, controller.text); },
                  child: const Text('记录决策'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  // ========================================================= 导出页面

  Widget _buildExportPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('导出成品', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('把已验证的文字写出为 EPUB 3 或 DOCX。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 20),
          _buildExportForm(),
        ],
      ),
    );
  }

  Widget _buildExportForm() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        DropdownButtonFormField<String>(
          value: _exportFormatEpub ? 'epub' : 'docx',
          items: <DropdownMenuItem<String>>[
            DropdownMenuItem(value: 'epub', child: const Text('EPUB 3')),
            DropdownMenuItem(value: 'docx', child: const Text('DOCX')),
          ],
          onChanged: (value) => setState(() => _exportFormatEpub = value == 'epub'),
          decoration: const InputDecoration(labelText: '导出格式'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _exportOutputController,
          decoration: const InputDecoration(
            labelText: '输出文件',
            hintText: '手动输入导出文件的完整路径',
            suffixIcon: const Icon(Icons.save_outlined),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _exportLanguageController,
          decoration: const InputDecoration(labelText: '语言（BCP 47，如 zh）'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _exportTitleController,
          decoration: const InputDecoration(labelText: '书名（可选）'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _exportAuthorController,
          decoration: const InputDecoration(labelText: '作者（可选）'),
        ),
        const SizedBox(height: 12),
        CheckboxListTile(
          initialValue: _exportLayoutChapter,
          title: const Text('按章节布局（EPUB）'),
          subtitle: const Text('关闭后每页一个文档（便于核对扫描）'),
          onChanged: (v) => setState(() => _exportLayoutChapter = v ?? true),
        ),
        CheckboxListTile(
          initialValue: _exportStrictAcceptance,
          title: const Text('严格接受校验'),
          subtitle: const Text('有待抽查的配对时拒绝导出（退出码 2）'),
          onChanged: (v) => setState(() => _exportStrictAcceptance = v ?? false),
        ),
        const SizedBox(height: 20),
        SizedBox(
          width: double.infinity,
          height: 48,
          child: ElevatedButton.icon(
            icon: Icon(_busy ? Icons.hourglass_empty : Icons.output),
            label: Text(_busy ? '导出中…' : '开始导出'),
            onPressed: _busy ||
                    _projectStorePath == null ||
                    _exportOutputController.text.isEmpty
                ? null
                : () => _runExport(
                      outputPath: _exportOutputController.text,
                      language: _exportLanguageController.text,
                      title: _exportTitleController.text,
                      author: _exportAuthorController.text,
                      layoutChapter: _exportLayoutChapter,
                      strictAcceptance: _exportStrictAcceptance,
                    ),
          ),
        ),
      ],
    );
  }

  // ========================================================= 设置页面

  Widget _buildSettingsPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('设置', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('配置命令行工具与运行时。首次使用请先运行运行时配置。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 20),
          ListTile(
            title: const Text('命令行工具'),
            subtitle: Text(_cliExecutable),
            trailing: IconButton(
              icon: const Icon(Icons.edit),
              onPressed: () async {
                final value = await _promptString('命令行工具路径', _cliExecutable);
                if (value != null && value.trim().isNotEmpty) {
                  await _settingsStore.saveCliExecutable(value.trim());
                  setState(() => _cliExecutable = value.trim());
                }
              },
            ),
          ),
          const Divider(),
          ListTile(
            title: const Text('运行时配置 (setup)'),
            subtitle: const Text('检测硬件、安装 OCR 运行环境与模型'),
            trailing: _runtimeLoading
                ? SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.play_arrow),
            onTap: _runtimeLoading ? null : () { _runSetup(); },
          ),
          if (_runtimeStatus != null) ...[
            const Divider(),
            const Text('运行时状态', style: TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text('根目录：${_runtimeStatus!.runtimeRoot}'),
            Text('存在：${_runtimeStatus!.runtimeRootExists}'),
            Text('uv 可用：${_runtimeStatus!.uvAvailable}'),
            const SizedBox(height: 8),
            Text('后端：', style: const TextStyle(fontWeight: FontWeight.w600)),
            ..._runtimeStatus!.backends.entries.map((entry) => Padding(
              padding: const EdgeInsets.only(left: 16, top: 4),
              child: Text('${entry.key}: ${entry.value.state} ${entry.value.environmentExists ? '' : '(环境未安装)'}',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
            )),
          ],
          const Divider(),
          ListTile(
            title: const Text('刷新运行时状态'),
            trailing: const Icon(Icons.refresh),
            onTap: () { _refreshRuntimeStatus(); },
          ),
        ],
      ),
    );
  }

  /// 简单的文本输入对话框。
  Future<String?> _promptString(String title, String initialValue) async {
    final controller = TextEditingController(text: initialValue);
    return showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: TextField(controller: controller, autofocus: true),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(context, controller.text), child: const Text('确定')),
        ],
      ),
    );
  }
}

/// 复核队列中的一条待处理项。
class ReviewQueueItem {
  const ReviewQueueItem({
    required this.description,
    this.pageIndex,
    this.pairIndex,
  });

  final String description;
  final int? pageIndex;
  final int? pairIndex;
}

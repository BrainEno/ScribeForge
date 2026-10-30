import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/candidate_comparison_navigation.dart';
import '../models/epub_candidate.dart';
import '../models/evaluation_progress.dart';
import '../models/text_diff.dart';
import '../services/candidate_comparison_store.dart';
import '../services/cli_executor.dart';
import '../services/cli_runner.dart';
import '../services/desktop_file_picker.dart';

/// A/B comparison is a human quality-evaluation sidecar, never an accepted OCR
/// decision. The screen deliberately has no `resolve` or project-write action.
class CandidateComparisonScreen extends StatefulWidget {
  const CandidateComparisonScreen({
    super.key,
    required this.sourcePdfPath,
    required this.storePath,
    required this.cliExecutable,
    required this.canGenerateCandidates,
    required this.cliExecutor,
    required this.filePicker,
    required this.comparisonStore,
    this.sourcePageCount,
    this.expectedSourceSha256,
    this.onOpenTrustedReview,
  });

  final String? sourcePdfPath;
  final String? storePath;
  final String cliExecutable;
  final bool canGenerateCandidates;
  final ScribeForgeCliExecutor cliExecutor;
  final DesktopFilePicker filePicker;
  final CandidateComparisonStore comparisonStore;
  final int? sourcePageCount;
  final String? expectedSourceSha256;
  final VoidCallback? onOpenTrustedReview;

  @override
  State<CandidateComparisonScreen> createState() =>
      _CandidateComparisonScreenState();
}

class _CandidateComparisonScreenState extends State<CandidateComparisonScreen> {
  EpubCandidate? _candidateA;
  EpubCandidate? _candidateB;
  int _currentPage = 0;
  Map<int, String> _pageDecisions = <int, String>{};
  String? _overallWinner;
  final TextEditingController _pageController = TextEditingController();
  final TextEditingController _notesController = TextEditingController();
  bool _busy = false;
  bool _saving = false;
  bool _candidateExportFailed = false;
  int _sessionVersion = 0;
  String? _sourceSha256;
  String? _error;
  String? _notice;
  String? _savedSidecarPath;

  @override
  void didUpdateWidget(covariant CandidateComparisonScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    final sourceFingerprintChanged =
        oldWidget.expectedSourceSha256 != null &&
        widget.expectedSourceSha256 != null &&
        widget.expectedSourceSha256 != oldWidget.expectedSourceSha256;
    if (widget.sourcePdfPath != oldWidget.sourcePdfPath ||
        widget.storePath != oldWidget.storePath ||
        sourceFingerprintChanged) {
      _sessionVersion++;
      _busy = false;
      _saving = false;
      _candidateExportFailed = false;
      _sourceSha256 = null;
      _candidateA = null;
      _candidateB = null;
      _currentPage = 0;
      _pageController.clear();
      _pageDecisions = <int, String>{};
      _overallWinner = null;
      _notesController.clear();
      _savedSidecarPath = null;
      _error = null;
      _notice = null;
    }
  }

  @override
  void dispose() {
    _pageController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  int get _pageCount {
    final sourcePageCount = widget.sourcePageCount;
    if (sourcePageCount != null && sourcePageCount > 0) return sourcePageCount;
    var highestPage = -1;
    for (final candidate in <EpubCandidate?>[_candidateA, _candidateB]) {
      if (candidate == null) continue;
      if (candidate.isSourcePageAligned) {
        for (final page in candidate.pages) {
          final sourcePage = page.sourcePageIndex;
          if (sourcePage != null) {
            highestPage = math.max(highestPage, sourcePage).toInt();
          }
        }
      } else {
        highestPage = math.max(highestPage, candidate.pages.length - 1).toInt();
      }
    }
    return highestPage + 1;
  }

  EpubPage? get _pageA => _candidateA?.pageForSourceIndex(_currentPage);
  EpubPage? get _pageB => _candidateB?.pageForSourceIndex(_currentPage);

  List<String> get _linesA => _pageA?.lines ?? const <String>[];
  List<String> get _linesB => _pageB?.lines ?? const <String>[];
  List<TextDiffRow> get _diffRows => diffLines(_linesA, _linesB);

  Future<void> _generateCandidates() async {
    final sourcePath = widget.sourcePdfPath;
    final storePath = widget.storePath;
    if (sourcePath == null ||
        storePath == null ||
        !widget.canGenerateCandidates ||
        _busy) {
      return;
    }

    final version = _sessionVersion;
    setState(() {
      _busy = true;
      _candidateExportFailed = false;
      _error = null;
      _notice = null;
    });
    try {
      final sourceHashBefore =
          await widget.comparisonStore.fingerprint(sourcePath);
      if (!mounted || version != _sessionVersion) return;
      final expectedSourceSha256 = widget.expectedSourceSha256;
      if (expectedSourceSha256 != null &&
          sourceHashBefore != expectedSourceSha256) {
        throw StateError(
          'The source PDF no longer matches the project SHA-256; reload or relink it before exporting candidates.',
        );
      }
      final outputDirectory =
          ScribeForgeCli.candidateOutputDirectoryForPdf(sourcePath);
      final output = StringBuffer();
      final exitCode = await widget.cliExecutor.run(
        executable: widget.cliExecutable,
        arguments: ScribeForgeCli.exportCandidateArguments(
          storePath: storePath,
          outputDirectory: outputDirectory,
        ),
        onOutput: output.write,
        onError: output.write,
      );
      if (!mounted || version != _sessionVersion) return;
      if (exitCode != 0) {
        throw StateError(
          output.toString().trim().isEmpty
              ? 'Candidate export exited with code $exitCode.'
              : output.toString().trim(),
        );
      }
      final sourceHashAfter =
          await widget.comparisonStore.fingerprint(sourcePath);
      if (!mounted || version != _sessionVersion) return;
      if (sourceHashAfter != sourceHashBefore) {
        throw StateError(
          'The source PDF changed while candidates were being exported; reload the source and retry.',
        );
      }
      final candidateA = await widget.comparisonStore.loadCandidate(
        ScribeForgeCli.candidateEpubPath(outputDirectory, 'mineru'),
      );
      final candidateB = await widget.comparisonStore.loadCandidate(
        ScribeForgeCli.candidateEpubPath(outputDirectory, 'paddleocr'),
      );
      if (!mounted || version != _sessionVersion) return;
      await _installCandidates(
        candidateA,
        candidateB,
        sourceSha256: sourceHashBefore,
      );
      if (!mounted || version != _sessionVersion) return;
      setState(() {
        _notice = '已加载未验证的 OCR 候选；页面评价只写入独立旁路文件。';
      });
    } catch (error) {
      if (!mounted || version != _sessionVersion) return;
      setState(() {
        _candidateExportFailed = true;
        _error = '生成候选 EPUB 失败：$error';
      });
    } finally {
      if (mounted && version == _sessionVersion) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _pickCandidate({required bool isCandidateA}) async {
    if (_busy || widget.sourcePdfPath == null) return;
    final version = _sessionVersion;
    final path = await widget.filePicker.pickFile(
      dialogTitle: isCandidateA ? '选择候选 A EPUB' : '选择候选 B EPUB',
      allowedExtensions: <String>['epub'],
    );
    if (!mounted || version != _sessionVersion || path == null) return;
    setState(() {
      _busy = true;
      _error = null;
      _notice = null;
    });
    try {
      final candidate = await widget.comparisonStore.loadCandidate(path);
      if (!mounted || version != _sessionVersion) return;
      final candidateA = isCandidateA ? candidate : _candidateA;
      final candidateB = isCandidateA ? _candidateB : candidate;
      if (candidateA != null && candidateB != null) {
        await _installCandidates(candidateA, candidateB);
      } else {
        setState(() {
          if (isCandidateA) {
            _candidateA = candidate;
          } else {
            _candidateB = candidate;
          }
          _currentPage = 0;
          _pageController.clear();
          _sourceSha256 = null;
          _resetEvaluation();
        });
      }
    } catch (error) {
      if (!mounted || version != _sessionVersion) return;
      setState(() => _error = '读取候选 EPUB 失败：$error');
    } finally {
      if (mounted && version == _sessionVersion) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _installCandidates(
    EpubCandidate candidateA,
    EpubCandidate candidateB, {
    String? sourceSha256,
  }) async {
    final version = _sessionVersion;
    var resolvedSourceSha256 = sourceSha256;
    setState(() {
      _candidateA = candidateA;
      _candidateB = candidateB;
      _currentPage = 0;
      _pageController.clear();
      _sourceSha256 = resolvedSourceSha256;
      _resetEvaluation();
      _error = null;
      _savedSidecarPath = null;
    });
    final sourcePath = widget.sourcePdfPath;
    if (resolvedSourceSha256 == null && sourcePath != null) {
      try {
        resolvedSourceSha256 =
            await widget.comparisonStore.fingerprint(sourcePath);
      } catch (error) {
        if (!mounted || version != _sessionVersion) return;
        setState(() => _error = '读取源文件 SHA-256 失败：$error');
        return;
      }
    }
    if (!mounted || version != _sessionVersion || resolvedSourceSha256 == null) {
      return;
    }
    setState(() => _sourceSha256 = resolvedSourceSha256);
    await _restoreMatchingEvaluation(candidateA, candidateB);
  }

  void _resetEvaluation() {
    _pageDecisions = <int, String>{};
    _overallWinner = null;
    _notesController.clear();
    _savedSidecarPath = null;
  }

  Future<void> _restoreMatchingEvaluation(
    EpubCandidate candidateA,
    EpubCandidate candidateB,
  ) async {
    final sourcePath = widget.sourcePdfPath;
    if (sourcePath == null) return;
    final version = _sessionVersion;
    try {
      final document =
          await widget.comparisonStore.readEvaluation(sourcePath);
      if (!mounted || version != _sessionVersion || document == null) return;
      final source = _asStringMap(document['source_pdf']);
      final savedA = _asStringMap(document['candidate_a']);
      final savedB = _asStringMap(document['candidate_b']);
      final sourceHash = await widget.comparisonStore.fingerprint(sourcePath);
      if (!mounted || version != _sessionVersion) return;
      final matches = source['path'] == sourcePath &&
          source['sha256'] == sourceHash &&
          savedA['path'] == candidateA.path &&
          savedA['sha256'] == candidateA.sha256 &&
          savedB['path'] == candidateB.path &&
          savedB['sha256'] == candidateB.sha256;
      if (!matches) return;

      final restored = <int, String>{};
      final rawDecisions = document['page_decisions'];
      if (rawDecisions is Map) {
        for (final entry in rawDecisions.entries) {
          final page = int.tryParse(entry.key.toString());
          final decision = entry.value;
          if (page != null &&
              page >= 0 &&
              page < _pageCount &&
              decision is String &&
              const <String>{'a', 'b', 'tie', 'review'}.contains(decision)) {
            restored[page] = decision;
          }
        }
      }
      final winner = document['overall_winner'];
      final notes = document['notes'];
      if (!mounted) return;
      setState(() {
        _sourceSha256 = sourceHash;
        _pageDecisions = restored;
        _overallWinner = winner is String &&
                const <String>{'a', 'b', 'tie', 'review'}.contains(winner)
            ? winner
            : null;
        _notesController.text = notes is String ? notes : '';
        _savedSidecarPath =
            '${sourcePath}.scribeforge-review.json';
        _notice = '已恢复与当前源文件及候选 SHA-256 匹配的人工评测。';
      });
    } catch (error) {
      if (!mounted || version != _sessionVersion) return;
      setState(() => _error = '读取已有候选评测失败：$error');
    }
  }

  Map<String, dynamic> _asStringMap(Object? value) => value is Map
      ? Map<String, dynamic>.from(value)
      : <String, dynamic>{};

  Future<void> _recordPageDecision(String decision) async {
    final decisions = Map<int, String>.from(_pageDecisions)
      ..[_currentPage] = decision;
    await _saveEvaluation(decisions, advance: true);
  }

  Future<void> _saveEvaluation(
    Map<int, String> decisions, {
    bool advance = false,
  }) async {
    final sourcePath = widget.sourcePdfPath;
    final sourceSha256 = _sourceSha256;
    final candidateA = _candidateA;
    final candidateB = _candidateB;
    if (_saving || sourcePath == null || candidateA == null || candidateB == null) {
      return;
    }
    if (sourceSha256 == null) {
      setState(() => _error = '无法读取源文件指纹；请重新加载源文件后再保存评测。');
      return;
    }
    final winner = _overallWinner;
    final notes = _notesController.text;
    final version = _sessionVersion;
    setState(() {
      _saving = true;
      _error = null;
      _notice = null;
    });
    try {
      final sidecar = await widget.comparisonStore.saveEvaluation(
        sourcePdfPath: sourcePath,
        candidateAPath: candidateA.path,
        candidateBPath: candidateB.path,
        candidateALabel: _candidateLabel(candidateA, 'A'),
        candidateBLabel: _candidateLabel(candidateB, 'B'),
        pageDecisions: decisions,
        overallWinner: winner,
        notes: notes,
        expectedSourceSha256: sourceSha256,
        expectedCandidateASha256: candidateA.sha256,
        expectedCandidateBSha256: candidateB.sha256,
      );
      if (!mounted || version != _sessionVersion) return;
      final nextPage = advance
          ? EvaluationProgress.nextPendingPage(
              pageCount: _pageCount,
              currentPage: _currentPage,
              decisions: decisions,
            )
          : null;
      setState(() {
        _pageDecisions = Map<int, String>.from(decisions);
        _savedSidecarPath = sidecar;
        _notice = '候选评测已保存；不改变项目决策或可信导出。';
        if (nextPage != null) _currentPage = nextPage;
      });
    } catch (error) {
      if (!mounted || version != _sessionVersion) return;
      setState(() => _error = '保存候选评测失败：$error');
    } finally {
      if (mounted && version == _sessionVersion) {
        setState(() => _saving = false);
      }
    }
  }

  String _candidateLabel(EpubCandidate candidate, String fallback) {
    final creator = candidate.creator?.trim();
    return creator == null || creator.isEmpty ? '候选 $fallback' : creator;
  }

  void _jumpToPage(String value) {
    final page = int.tryParse(value.trim());
    if (page == null || page < 1 || page > _pageCount) {
      setState(() => _error = '页码须介于 1 和 $_pageCount 之间。');
      return;
    }
    setState(() {
      _currentPage = page - 1;
      _error = null;
    });
  }

  void _nextDifferentPage() {
    final candidateA = _candidateA;
    final candidateB = _candidateB;
    if (candidateA == null || candidateB == null) return;
    final nextPage = CandidateComparisonNavigation.nextDifferingPage(
      pageCount: _pageCount,
      currentPage: _currentPage,
      candidateA: candidateA,
      candidateB: candidateB,
    );
    if (nextPage == null) {
      setState(() => _notice = '其他页面没有文本差异。');
      return;
    }
    setState(() {
      _currentPage = nextPage;
      _error = null;
      _notice = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final sourcePath = widget.sourcePdfPath;
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text(
            'OCR 候选比较',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          Text(
            '并排比较原始 OCR EPUB，并单独保存人工质量评测。A/B 评测不会核准 OCR 内容、修改项目证据或解除可信导出的阻塞。',
            style: TextStyle(color: Colors.grey.shade700, fontSize: 13),
          ),
          const SizedBox(height: 12),
          if (sourcePath == null)
            const Expanded(
              child: Center(child: Text('请先导入书籍或打开项目，再比较 OCR 候选。')),
            )
          else ...[
            _buildCandidateActions(),
            if (_error != null || _notice != null) ...[
              const SizedBox(height: 8),
              _buildMessage(),
            ],
            const SizedBox(height: 12),
            Expanded(
              child: _candidateA == null || _candidateB == null
                  ? _buildCandidatePlaceholder()
                  : _buildComparison(),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildCandidateActions() {
    final canGenerate = widget.storePath != null &&
        widget.canGenerateCandidates &&
        !_busy;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: <Widget>[
        OutlinedButton.icon(
          onPressed: _busy ? null : () => _pickCandidate(isCandidateA: true),
          icon: const Icon(Icons.file_open_outlined),
          label: const Text('选择候选 A'),
        ),
        OutlinedButton.icon(
          onPressed: _busy ? null : () => _pickCandidate(isCandidateA: false),
          icon: const Icon(Icons.file_open_outlined),
          label: const Text('选择候选 B'),
        ),
        ElevatedButton.icon(
          onPressed: canGenerate ? _generateCandidates : null,
          icon: Icon(_busy ? Icons.hourglass_empty : Icons.compare_arrows),
          label: Text(
            _candidateExportFailed ? '重试生成候选' : '生成 OCR 候选 EPUB',
          ),
        ),
        if (_busy || _saving)
          const SizedBox(
            width: 20,
            height: 20,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        if (widget.onOpenTrustedReview != null)
          TextButton.icon(
            onPressed: widget.onOpenTrustedReview,
            icon: const Icon(Icons.fact_check_outlined),
            label: const Text('打开可信复核队列'),
          ),
      ],
    );
  }

  Widget _buildMessage() => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: _error == null ? Colors.green.shade50 : Colors.amber.shade50,
          borderRadius: BorderRadius.circular(8),
        ),
        child: SelectableText(_error ?? _notice!),
      );

  Widget _buildCandidatePlaceholder() {
    final candidateA = _candidateA;
    final candidateB = _candidateB;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 640),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text(
                  '加载两份未验证候选 EPUB 后即可开始比较。',
                  style: TextStyle(fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 8),
                Text('候选 A：${candidateA?.title ?? '尚未选择'}'),
                Text('候选 B：${candidateB?.title ?? '尚未选择'}'),
                const SizedBox(height: 12),
                Text(
                  widget.canGenerateCandidates
                      ? '可直接从当前项目导出 MinerU 与 PaddleOCR 的原始候选。'
                      : '一键生成要求所有页面都已有双引擎 OCR 结果；也可以手动加载现有 EPUB。',
                  style: TextStyle(color: Colors.grey.shade700, fontSize: 12),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildComparison() {
    final candidateA = _candidateA!;
    final candidateB = _candidateB!;
    final summary = summarizeDiff(_diffRows, _linesA, _linesB);
    final pageDecision = _pageDecisions[_currentPage];
    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(child: _candidateCard('A', candidateA)),
              const SizedBox(width: 10),
              Expanded(child: _candidateCard('B', candidateB)),
            ],
          ),
          if (!candidateA.isSourcePageAligned ||
              !candidateB.isSourcePageAligned)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                '至少一份 EPUB 未用 page-N 文件名标记源页；当前按 spine 顺序对齐，请谨慎解释跨页差异。',
                style: TextStyle(color: Colors.orange.shade900, fontSize: 12),
              ),
            ),
          const SizedBox(height: 8),
          _buildNavigation(pageDecision),
          const SizedBox(height: 8),
          Text(
            '本页：${summary.sharedLines} 行相同 · ${summary.changedRows} 行差异',
            style: TextStyle(color: Colors.grey.shade700, fontSize: 12),
          ),
          const SizedBox(height: 6),
          _buildDiffTable(),
          const SizedBox(height: 16),
          _buildEvaluationControls(pageDecision),
          const SizedBox(height: 12),
          _buildOverallEvaluation(),
        ],
      ),
    );
  }

  Widget _candidateCard(String side, EpubCandidate candidate) => Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text('候选 $side', style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              Text(candidate.title, maxLines: 2, overflow: TextOverflow.ellipsis),
              if (candidate.creator != null && candidate.creator!.isNotEmpty)
                Text(
                  '引擎：${candidate.creator}',
                  style: TextStyle(color: Colors.grey.shade700, fontSize: 11),
                ),
              Text(
                'SHA-256 ${candidate.sha256.substring(0, 12)}… · 未验证',
                style: TextStyle(color: Colors.grey.shade600, fontSize: 10),
              ),
            ],
          ),
        ),
      );

  Widget _buildNavigation(String? pageDecision) {
    final pageCount = _pageCount;
    final canGoNext = _currentPage + 1 < pageCount;
    final canGoPrevious = _currentPage > 0;
    return Wrap(
      crossAxisAlignment: WrapCrossAlignment.center,
      spacing: 6,
      runSpacing: 6,
      children: <Widget>[
        IconButton(
          tooltip: '上一页',
          onPressed: canGoPrevious
              ? () => setState(() => _currentPage--)
              : null,
          icon: const Icon(Icons.chevron_left),
        ),
        Text('第 ${_currentPage + 1} 页 / $pageCount'),
        IconButton(
          tooltip: '下一页',
          onPressed: canGoNext ? () => setState(() => _currentPage++) : null,
          icon: const Icon(Icons.chevron_right),
        ),
        Tooltip(
          message: '下一处差异',
          child: OutlinedButton.icon(
            onPressed: _nextDifferentPage,
            icon: const Icon(Icons.difference_outlined, size: 18),
            label: const Text('下一处差异'),
          ),
        ),
        SizedBox(
          width: 110,
          child: TextField(
            key: const ValueKey('candidate-page-jump'),
            controller: _pageController,
            keyboardType: TextInputType.number,
            textInputAction: TextInputAction.go,
            onSubmitted: _jumpToPage,
            decoration: const InputDecoration(
              labelText: '跳转页码',
              isDense: true,
            ),
          ),
        ),
        OutlinedButton(
          onPressed: () => _jumpToPage(_pageController.text),
          child: const Text('跳转'),
        ),
        if (pageDecision != null)
          Chip(label: Text('本页：${_decisionLabel(pageDecision)}')),
      ],
    );
  }

  Widget _buildDiffTable() {
    if (_diffRows.isEmpty) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(16),
          child: Text('本页两份候选都没有可比较的文本。'),
        ),
      );
    }
    return Column(
      children: <Widget>[
        for (final row in _diffRows)
          Padding(
            padding: const EdgeInsets.only(bottom: 3),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Expanded(child: _diffCell(row.left, changed: row.changed)),
                const SizedBox(width: 6),
                Expanded(child: _diffCell(row.right, changed: row.changed)),
              ],
            ),
          ),
      ],
    );
  }

  Widget _diffCell(String? text, {required bool changed}) => Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
        decoration: BoxDecoration(
          color: changed ? Colors.amber.shade50 : Colors.white,
          borderRadius: BorderRadius.circular(5),
          border: Border.all(
            color: changed ? Colors.amber.shade200 : Colors.grey.shade200,
          ),
        ),
        child: SelectableText(
          text ?? '∅（此处无对应行）',
          style: TextStyle(
            color: text == null ? Colors.grey.shade700 : null,
            fontStyle: text == null ? FontStyle.italic : null,
          ),
        ),
      );

  Widget _buildEvaluationControls(String? pageDecision) {
    final pageCount = _pageCount;
    final canRecord = !_busy && !_saving && _sourceSha256 != null;
    final decided = EvaluationProgress.decidedCount(pageCount, _pageDecisions);
    final deferred = EvaluationProgress.deferredCount(pageCount, _pageDecisions);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                const Expanded(
                  child: Text(
                    '人工 A/B 评测',
                    style: TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                Text('$decided / $pageCount 页已完成 · $deferred 页暂缓'),
              ],
            ),
            const SizedBox(height: 4),
            LinearProgressIndicator(
              value: pageCount == 0 ? 0 : decided / pageCount,
              minHeight: 3,
            ),
            const SizedBox(height: 6),
            Text(
              '此选择只比较候选质量，不是对任一 OCR 文本的接受决定。',
              style: TextStyle(color: Colors.grey.shade700, fontSize: 12),
            ),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: <Widget>[
                ElevatedButton(
                  onPressed: canRecord ? () => _recordPageDecision('a') : null,
                  child: Text(pageDecision == 'a' ? '✓ 候选 A 更好' : '候选 A 更好'),
                ),
                ElevatedButton(
                  onPressed: canRecord ? () => _recordPageDecision('b') : null,
                  child: Text(pageDecision == 'b' ? '✓ 候选 B 更好' : '候选 B 更好'),
                ),
                OutlinedButton(
                  onPressed: canRecord ? () => _recordPageDecision('tie') : null,
                  child: Text(pageDecision == 'tie' ? '✓ 两者相当' : '两者相当'),
                ),
                OutlinedButton(
                  onPressed:
                      canRecord ? () => _recordPageDecision('review') : null,
                  child: Text(pageDecision == 'review' ? '✓ 稍后再评' : '稍后再评'),
                ),
              ],
            ),
            if (_savedSidecarPath != null) ...[
              const SizedBox(height: 6),
              SelectableText(
                '评测旁路文件：$_savedSidecarPath',
                style: TextStyle(color: Colors.grey.shade600, fontSize: 11),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildOverallEvaluation() {
    final canSave = !_busy && !_saving && _sourceSha256 != null;
    return Card(
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Text('整本书评价', style: TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 8),
              DropdownButtonFormField<String>(
                initialValue: _overallWinner,
                decoration: const InputDecoration(labelText: '总体比较结果'),
                items: const <DropdownMenuItem<String>>[
                  DropdownMenuItem(value: 'a', child: Text('候选 A 更优')),
                  DropdownMenuItem(value: 'b', child: Text('候选 B 更优')),
                  DropdownMenuItem(value: 'tie', child: Text('两者相当')),
                  DropdownMenuItem(value: 'review', child: Text('暂不判断')),
                ],
                onChanged: canSave
                    ? (value) => setState(() => _overallWinner = value)
                    : null,
              ),
              const SizedBox(height: 8),
              TextField(
                key: const ValueKey('candidate-evaluation-notes'),
                controller: _notesController,
                enabled: canSave,
                minLines: 1,
                maxLines: 3,
                decoration: const InputDecoration(
                  labelText: '评测备注',
                  hintText: '记录候选差异、缺页或版式表现；不会修改 OCR 证据。',
                ),
              ),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerRight,
                child: OutlinedButton.icon(
                  onPressed:
                      canSave ? () => _saveEvaluation(_pageDecisions) : null,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('保存整本书评价与备注'),
                ),
              ),
            ],
          ),
        ),
      );
  }

  String _decisionLabel(String value) => switch (value) {
        'a' => '候选 A 更好',
        'b' => '候选 B 更好',
        'tie' => '两者相当',
        'review' => '稍后再评',
        _ => value,
      };
}

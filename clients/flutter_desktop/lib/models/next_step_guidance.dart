/// 主窗口"下一步"引导条可采取的操作。
enum NextStepAction {
  none,
  configureBackend,
  initEnvironment,
  importBook,
  resumeLastProject,
  relinkSource,
  goProcess,
  goReview,
  goExport,
}

extension NextStepActionLabel on NextStepAction {
  String get buttonLabel => switch (this) {
    NextStepAction.none => '',
    NextStepAction.configureBackend => '去设置',
    NextStepAction.initEnvironment => '下载并配置',
    NextStepAction.importBook => '去导入',
    NextStepAction.resumeLastProject => '继续上次项目',
    NextStepAction.relinkSource => '去关联源文件',
    NextStepAction.goProcess => '去处理',
    NextStepAction.goReview => '去复核',
    NextStepAction.goExport => '去导出',
  };
}

/// 一条"下一步"建议：一句中文说明 + 一个可点击操作。
class NextStepGuidance {
  const NextStepGuidance(this.action, this.message, {this.count});

  final NextStepAction action;
  final String message;
  final int? count;
}

/// 由原始状态计算唯一的下一步建议（纯函数，便于穷举单测）。
NextStepGuidance nextStepGuidance({
  required bool backendChecked,
  required bool backendLaunchFailed,
  required bool runtimeSetupRequired,
  required bool hasProject,
  required bool hasSource,
  required bool sourceMismatch,
  required int pagesWithBothOcr,
  required int pagesTotal,
  required int reviewQueued,
  bool importIncomplete = false,
  bool sourceExtentMismatch = false,
  bool sourceExtentUnknown = false,
  String? lastProjectStore,
}) {
  if (!backendChecked) {
    return const NextStepGuidance(NextStepAction.none, '');
  }
  if (backendLaunchFailed) {
    return const NextStepGuidance(
      NextStepAction.configureBackend,
      '命令行工具无法启动，请到「设置」检查命令行工具路径。',
    );
  }
  if (runtimeSetupRequired) {
    return const NextStepGuidance(
      NextStepAction.initEnvironment,
      '首次使用：请先初始化 OCR 环境，完成后即可导入并处理书籍。',
    );
  }
  if (!hasProject && !hasSource && lastProjectStore != null) {
    return NextStepGuidance(
      NextStepAction.resumeLastProject,
      '上次的项目还在：$lastProjectStore，一键继续。',
    );
  }
  if (!hasProject && !hasSource) {
    return const NextStepGuidance(
      NextStepAction.importBook,
      '导入一本 PDF 或图片书籍开始，或打开已有项目继续。',
    );
  }
  if (sourceMismatch) {
    return const NextStepGuidance(
      NextStepAction.relinkSource,
      '源文件与项目记录不匹配，请重新关联源文件（需 SHA-256 相同）。',
    );
  }
  if (hasProject && sourceExtentMismatch) {
    return const NextStepGuidance(
      NextStepAction.goProcess,
      '项目页数与原书不一致，请先恢复备份或重新导入原书。',
    );
  }
  if (hasProject && importIncomplete) {
    return const NextStepGuidance(NextStepAction.goProcess, '源文件导入尚未完成，去继续处理。');
  }
  if (hasProject && sourceExtentUnknown) {
    return const NextStepGuidance(
      NextStepAction.goProcess,
      '原书总页数尚未核实；请用原文件继续处理或检查导出文件。',
    );
  }
  if (hasProject && pagesWithBothOcr < pagesTotal) {
    return const NextStepGuidance(
      NextStepAction.goProcess,
      '还有页面未完成双引擎 OCR，去处理。',
    );
  }
  if (!hasProject && hasSource) {
    return const NextStepGuidance(NextStepAction.goProcess, '已选择源文件，去处理页面。');
  }
  if (reviewQueued > 0) {
    return NextStepGuidance(
      NextStepAction.goReview,
      '复核队列还有 $reviewQueued 项待处理，去复核。',
      count: reviewQueued,
    );
  }
  return const NextStepGuidance(
    NextStepAction.goExport,
    '所有页面已完成复核，可以导出 EPUB / DOCX 了。',
  );
}

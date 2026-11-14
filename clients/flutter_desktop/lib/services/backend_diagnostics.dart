class DesktopBackendDiagnosis {
  const DesktopBackendDiagnosis({
    required this.message,
    required this.requiresRuntimeSetup,
  });

  final String message;
  final bool requiresRuntimeSetup;
}

DesktopBackendDiagnosis diagnoseCoreLaunchFailure(
  Object error,
  String executable,
) {
  final detail = error.toString();
  if (detail.contains('ProcessException')) {
    return DesktopBackendDiagnosis(
      message:
          '找不到后端命令行工具 `$executable`（系统找不到指定的文件）。\n\n'
          '请前往「设置」点击「初始化环境」自动创建后端环境（会自动探测可用的 Python，'
          '并避开 Windows Microsoft Store 的 python 占位符）；'
          '完整桌面安装包应已内置后端，若仍提示找不到，请检查设置中的「命令行工具」路径是否正确。\n'
          '技术详情：$detail',
      requiresRuntimeSetup: true,
    );
  }
  return DesktopBackendDiagnosis(
    message:
        '桌面处理核心未连接（$executable）。请前往“设置”检查内置组件并初始化环境；'
        '完整桌面安装包不需要预装 Python。技术详情：$detail',
    requiresRuntimeSetup: true,
  );
}

DesktopBackendDiagnosis diagnoseProcessingFailure(String output) {
  final detail = output.trim();
  final normalized = detail.toLowerCase();
  const runtimeMarkers = <String>[
    'modulenotfounderror',
    'no module named',
    'backend not ready',
    'is not ready',
    'python executable is missing',
    'worker source bundle is missing',
    'uv bootstrap tool',
    'failed to execute script',
  ];
  final runtimeFailure = runtimeMarkers.any(normalized.contains);
  if (!runtimeFailure) {
    return DesktopBackendDiagnosis(
      message: detail.isEmpty ? '处理失败，命令行工具没有返回错误详情。' : detail,
      requiresRuntimeSetup: false,
    );
  }

  final technicalDetail = detail.length <= 1200
      ? detail
      : detail.substring(detail.length - 1200);
  return DesktopBackendDiagnosis(
    message:
        'OCR 运行环境尚未就绪或组件不完整。请前往“设置 → 初始化环境”安装或修复所需组件，'
        '完成后重试；原始 PDF 和已保存的项目证据不会因此被改写。\n$technicalDetail',
    requiresRuntimeSetup: true,
  );
}

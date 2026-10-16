# 依赖与上游参考

## 安装分层

基础包只安装 NumPy、Pillow。Python 自带 HTTP、SQLite、TOML 解析与线程池。可选依赖在 `pyproject.toml` 的 extras 声明：

| Extra | 用途 |
|---|---|
| `documents` | PyMuPDF 解析/渲染 PDF、python-docx 提取 DOCX |
| `video` | OpenCV 抽帧、faster-whisper 转写 |
| `ocr` | RapidOCR ONNX Runtime OCR |
| `text` | Sentence Transformers 文字向量 |
| `wemm` | Transformers 5.2.0、qwen-vl-utils 0.0.14、Sentence Transformers 5.7.0、Accelerate |

PyTorch 由部署者按 CUDA/ROCm 平台先安装。基础依赖使用兼容版本范围，WeMM 关键接口依赖固定版本；本仓库不是跨硬件统一锁定的完整环境镜像。验收通过后可另存 `python -m pip freeze`，发布前确认清单不含私有 URL、令牌或本地路径。

## 官方资料

- [WeMM-Embedding 官方仓库](https://github.com/Tencent/WeMM-Embedding)
- [WeMM 2B 模型卡](https://huggingface.co/tencent/WeMM-Embedding-2B)：图文编码及 2,048 维输出。
- [WeMM 4B 模型卡](https://huggingface.co/tencent/WeMM-Embedding-4B)：图文编码及 2,560 维输出。
- [PyTorch 安装](https://pytorch.org/get-started/locally/)
- [AMD Radeon/ROCm 文档](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：CTranslate2 语音转写。
- [RapidOCR](https://github.com/RapidAI/RapidOCR)
- [PyMuPDF](https://pymupdf.readthedocs.io/)
- [SQLite FTS5](https://www.sqlite.org/fts5.html)

上游模型代码与权重、Python 包和系统工具分别受其许可证约束。特别是 PDF 相关可选依赖的许可条件应在分发前核对；不要认为仓库中没有附带权重就免于检查依赖许可。项目本身尚未指定对外授权许可证。

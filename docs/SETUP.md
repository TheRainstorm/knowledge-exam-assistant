# 从零搭建

教程面向 Linux。建议先用虚构 Markdown 示例跑通，再逐步启用视频、OCR、ASR 和 WeMM，避免同时排查多个环境问题。所有路径、模型地址与密钥都由你自行配置。

## 1. 系统准备

Ubuntu 24.04 示例：

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv git ffmpeg libglib2.0-0 libgl1
# 只有导入 PPTX 时才需要：
sudo apt install -y libreoffice-impress
```

其他发行版安装对应软件。已有 Python 3.11/3.12 的 Conda 环境也可以；不要覆盖系统 Python。确认：

```bash
python3.12 --version
ffmpeg -version
python3.12 -c 'import sqlite3; c=sqlite3.connect(":memory:"); c.execute("CREATE VIRTUAL TABLE t USING fts5(x)"); print("FTS5 OK")'
```

## 2. 放置仓库与创建环境

把本仓库放到 `$HOME/knowledge-exam-assistant`。如果从远程 Git 获取，使用你自己创建的仓库 URL；不需要任何原部署机器上的文件。

```bash
cd "$HOME/knowledge-exam-assistant"
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp config.example.toml config.toml
mkdir -p private/inputs
cp examples/demo.md private/inputs/
kexam doctor
```

`config.toml` 的路径相对于配置文件所在目录，不随 shell 当前目录改变。`private/`、`data/`、`models/`、`.env` 和 `config.toml` 均已加入 `.gitignore`。

## 3. 验证无需 GPU 的最小闭环

```bash
kexam ingest private/inputs
kexam index
kexam search '探测器备份保留多久'
kexam serve
```

浏览器打开 `http://127.0.0.1:8765/`，应能检索到“十四天”。打开 `/handbook` 应能阅读原文。终端按 Ctrl+C 停止服务。

这个阶段 `embedding.backend="none"`，只建立中文关键词索引；不下载任何大模型。`data/knowledge.md` 是资料提取后的原文汇编，不是人工审校的讲义。

不要直接将整个 `examples/` 导入：里面的 `practice.json` 包含虚构评测答案，只能用于独立评分，不能成为检索证据。

## 4. 接入截图识别与答题模型

编辑 `config.toml`：

```toml
[quiz]
endpoint = "https://your-provider.example/v1/chat/completions"
model = "your-image-capable-model"
api_key_env = "KEXAM_API_KEY"
default_concurrency = 3
max_model_requests = 8
```

这是占位地址，必须替换。也可使用自建模型服务的 HTTP 地址。需要满足：

1. 接收 `POST /v1/chat/completions` 风格请求，支持 `messages` 中的文字和 `image_url` Base64 图片。
2. 返回 `choices[0].message.content`，内容为遵循提示的 JSON 文本。
3. 支持足够输出长度（识别最多 8,000 输出 token），能逐项覆盖单选、多选选项。

只有 `/v1/responses` 接口的服务不能直接使用，需兼容代理或单独适配。只有文本能力的模型不能完成截图识别。有些服务商支持 `thinking={"type":"disabled"}`，仅在确认兼容时添加 `thinking="disabled"`，否则不配置。

设置环境变量并启动：

```bash
read -rsp 'Vision API key: ' KEXAM_API_KEY; echo
export KEXAM_API_KEY
kexam serve
```

无鉴权的可信本机 API 可留空密钥。CLI 不自动加载 `.env`。

打开 `/quiz`，截取虚构示例中的一道自编题，粘贴进去，设置并发数并提交。检查识别的题干、否定词、题型、全部选项，核对证据。默认显示资料不足时的模型推断；需要严格模式时取消勾选。截图识别、作答和复核会调用你配置的 API，传输题图及检索到的资料片段/图片，可能计费。

## 5. 导入自己的文档与视频

先安装所需扩展：

```bash
python -m pip install -e '.[documents,video,ocr]'
```

将想纳入同一知识库的全部源文件放入 `private/inputs/`，支持子目录。修改 `[ingest]`：

```toml
[ingest]
sample_seconds = 5.0
frame_change_threshold = 6.0
compare_roi = [0.0, 0.0, 1.0, 1.0]
ocr = true
transcribe = true
asr_model = "small"
asr_language = "zh"
asr_device = "cpu"
asr_compute_type = "int8"
pdf_dpi = 120
```

CPU ASR 不占用 WeMM 显存，但长视频需要时间。可将 `asr_model` 改为本地 CTranslate2 Whisper 模型目录；首次使用模型名可能需要下载。OCR 首次运行也可能下载权重，应预先确认网络和许可。

`sample_seconds` 是视频采样间隔，不是“每一页都保证捕获”。快速翻页、动画、长时间移动的讲师会影响抽帧。可以降低至 2 秒；若讲师区域导致重复，用 `compare_roi` 指定仅用于比较变化的 PPT 区域。最终保存的是完整画面。

PDF 每页保存原图并提取内嵌文字，扫描 PDF 需 OCR；DOCX 支持段落与表格文字，内嵌图表暂不解析；PPTX 由 LibreOffice 转 PDF 后导入，复杂字体/动画不保证还原。加密或损坏文件会让本次导入失败，先处理该文件再重跑。

重新执行：

```bash
kexam ingest private/inputs
kexam index
kexam serve
```

每次 `ingest` 发布的是这一个输入目录的完整清单。不要分两次导入两个独立目录后期待自动合并；应统一放到同一目录。未变的源文件通过哈希复用提取缓存，设置变化会重新提取。导入失败时保留上一个完整清单。更新源资料后，停止旧网页、重新导入和建索引，再启动网页；不要一边答题一边升级索引。

## 6. 可选：仅文字语义检索

```bash
python -m pip install -e '.[text]'
```

配置：

```toml
[embedding]
backend = "text"
model = "BAAI/bge-small-zh-v1.5"
revision = "main"
device = "cpu"
```

首次加载会下载模型。正式部署建议将 `revision` 固定到上游的准确提交；切换模型或版本后重新建索引。图像通过 OCR 文字参与检索，这条路径没有原图向量。

```bash
kexam index
kexam serve
```

## 7. 可选：WeMM 多模态检索

### 7.1 先验证显卡与 PyTorch

不要先安装普通 CPU 版 torch 再期待自动启用 GPU。先根据 [PyTorch 安装选择器](https://pytorch.org/get-started/locally/) 安装适配驱动的 CUDA/ROCm PyTorch。AMD 先核对 [Radeon/ROCm 支持矩阵](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/)，操作系统、GPU、ROCm、Python、PyTorch 版本必须组成支持的组合；安装驱动可能需要重启机器，应在维护窗口完成。

安装选择器给出的命令后检查：

```bash
python - <<'PY'
import torch
print('torch:', torch.__version__)
print('CUDA:', torch.version.cuda, 'HIP:', torch.version.hip)
assert torch.cuda.is_available(), 'GPU is not available'
print(torch.cuda.get_device_name(0))
print(torch.ones(4, device='cuda').sum().item())
PY
```

这一步必须通过。AMD 与 NVIDIA 都配置 `device="cuda"`。本仓库不自动安装/重启驱动，也不通过设置架构伪装环境变量绕过设备支持。

### 7.2 安装模型运行依赖并下载固定快照

在上述同一 Python 环境中：

```bash
python -m pip install -e '.[wemm]'
python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='tencent/WeMM-Embedding-2B',
    revision='bbd6cd4bf52cfc6716f752a2df80b2706720bd95',
    local_dir='models/wemm-2b',
)
PY
```

代码采用官方 `AutoProcessor`、`AutoModel.embedding` 和 L2 归一化，加载本地模型时需要 `trust_remote_code=True`。部署前应阅读固定快照中的模型代码与许可证。该选项执行上游 Python，不是任意模型文件都可以安全替换。

### 7.3 切换配置，重建索引，启动

```toml
[embedding]
backend = "wemm"
model = "./models/wemm-2b"
revision = "bbd6cd4bf52cfc6716f752a2df80b2706720bd95"
device = "cuda"
```

```bash
kexam index
kexam serve
```

两条命令依次运行。索引构建结束后进程退出释放显存；服务启动再加载一次模型，常驻处理检索。不要同时开多个 WeMM 服务，也不要让其他大型模型占满显存。

图片使用原图向量，段落使用文字向量，文字问题与两类向量分别比较，再与词法排名融合。WeMM 不转录音频，音频知识来自前面的 ASR。

首次建索引需要遍历全部资料；向量按模型配置和输入内容哈希缓存，重建可复用。`config.toml` 中声明的 revision 必须与本地下载的真实快照一致，不要原地替换权重而保持同一配置。

### 7.4 使用 4B

下载另一个目录：

```bash
python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='tencent/WeMM-Embedding-4B',
    revision='41a4fafdd26252ddff05c025413af7e61e26f08e',
    local_dir='models/wemm-4b',
)
PY
```

把 `model` 改成 `./models/wemm-4b`、`revision` 改成上面的 4B 提交，停止旧服务，运行 `kexam index`，完成后 `kexam serve`。2B 与 4B 向量维度不同，不能混用索引。先用少量虚构资料测试显存和延迟，再处理完整库。

## 8. 配置后台常驻

模板假定仓库在 `$HOME/knowledge-exam-assistant`，虚拟环境在其中 `.venv`。路径不同请修改 `WorkingDirectory`、`ExecStart` 和 `EnvironmentFile`。

```bash
mkdir -p ~/.config/systemd/user
cp deploy/kexam.service ~/.config/systemd/user/
cp .env.example .env
chmod 600 .env
# 使用编辑器在 .env 中填写 KEXAM_API_KEY=...，不要提交该文件。
systemctl --user daemon-reload
systemctl --user enable --now kexam.service
systemctl --user status kexam.service
journalctl --user -u kexam.service -n 80 --no-pager
```

若需登出后继续运行，管理员可执行 `sudo loginctl enable-linger "$USER"`。部署前应先通过前台验证，后台服务不会替你导入或建索引。启动前确保没有前台实例占用端口。

## 9. 从自己电脑访问 Linux 服务

服务只监听服务器 `127.0.0.1`。在客户端执行（替换账号和主机）：

```bash
ssh -N -L 8765:127.0.0.1:8765 user@your-server
```

浏览器打开客户端 `http://127.0.0.1:8765/`。非默认 SSH 端口添加 `-p`。如果客户端 8765 已占用，应同步修改服务器配置端口和转发两端端口；当前 Host 校验要求浏览器端口与服务端端口一致。

不建议直接将此 HTTP 服务映射到公网。它没有多用户登录、租户隔离或生产级授权机制。

## 10. 最后验收

```bash
python -m unittest discover -s tests -v
curl -f http://127.0.0.1:8765/api/health
curl -f http://127.0.0.1:8765/api/quiz/status
```

人工检查：能搜到已知原文；相关图片能打开；视频能跳至来源时间；截图题干/选项识别准确；多图并发持续补位；无法证明的答案标为需复核；导出文件包含同一批结果。

接口显示 `configured=true` 只表示配置格式完整，不代表 API 可用、额度足够或确实支持视觉。最后必须做一次自己的虚构截图实测。

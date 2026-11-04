# InnerVision AI

InnerVision AI 是一个跑在 Windows 本机上的私人成长助手。感恩日记、愿景卡片、释放练习和行动任务都保存在这台电脑上，不调用付费云端模型。

当前版本包含基础记录，也包含愿景板拖动、趋势图、每周成长报告、晨间晚间仪式、本地备份恢复，以及可选的本机语音。

## 能做什么

- 每天记下三件感恩的事，并查看这一周的原文回顾。
- 建立愿景卡片，加入分类、目标日期、里程碑和本地图片，再关联到行动。
- 按固定步骤做释放练习：觉察、允许、探索能否与是否愿意放下、练习后反思。每次只有一个问题，可以暂停、跳过或结束。
- 把事情分成 P1（紧急且重要）和 P2（为未来积累），标记完成并做每周数量回顾。
- 在对话里识别这四件事。助手只能提出写入；点“确认写入”之后才会进数据库。
- 在愿景板上拖动图片卡片，或使用前移、后移。松手或点击后，新顺序写入本机。
- 查看感恩天数、P1/P2 完成数量和释放练习强度。图上没有日记正文。
- 生成本周成长报告。没有本地模型时使用固定模板；确认后才保存。
- 做晨间或晚间仪式。每次一个问题，可以暂停、跳过或结束。
- 在“数据与隐私”里创建本地备份。恢复前要输入“恢复本地备份”并再确认一次。
- 可选安装本机语音。没有安装时，界面只显示说明，不会录音，也不会把声音发到网上。

释放练习参考了常见的基础问题顺序，文案是原创引导，不是医疗程序，也不承诺显化或疗愈结果。感到有即时危险时，练习会停止，并提示联系当地紧急服务和危机热线。

## 隐私

- 数据库、图片和日志都在本机数据目录，默认是 `%USERPROFILE%\.innervision`。
- Streamlit 和 MCP 只绑定 `127.0.0.1`。
- Ollama 地址必须是本机；指向其他主机的配置会被拒绝。
- 助手不能执行系统命令，也不能随意读写文件。
- 调试日志只记动作名称和结果，不记日记正文。
- “数据与隐私”页面可以导出 ZIP，创建带时间的本地备份，或在输入“删除全部本地数据”并再次确认后清空记录和备份。
- 恢复备份会覆盖当前记录，因此要输入“恢复本地备份”并再点一次确认。

## PowerShell 安装与启动

需要 Python 3.11 或更高版本。可选安装 [Ollama](https://ollama.com/)；没有它时，记录和固定引导仍然可用，只是不会改写句子。

```powershell
cd C:\Users\chits\Projects\innervision-ai
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pytest
streamlit run app.py
```

浏览器打开 `http://127.0.0.1:8501`。如果 PowerShell 禁止激活虚拟环境，可以改用：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m streamlit run app.py
```

想用本地模型时：

```powershell
ollama pull qwen2.5:7b
$env:INNERVISION_OLLAMA_MODEL = "qwen2.5:7b"
```

模型默认请求 `http://127.0.0.1:11434`。设置 `INNERVISION_ENABLE_LLM=0` 可以完全关闭模型调用。没有模型时，成长报告使用固定模板。

想用本机语音时再安装可选依赖。音频只在这台电脑上转写；第一次使用会把语音模型下载到数据目录。

```powershell
pip install -r requirements-voice.txt
```

单独启动 MCP 服务：

```powershell
$env:PYTHONPATH = "src"
python -m innervision.mcp
```

它只监听 `127.0.0.1:8765`。

## 数据位置

| 内容 | 位置 |
| --- | --- |
| 数据库 | `%USERPROFILE%\.innervision\innervision.db` |
| 愿景图片 | `%USERPROFILE%\.innervision\images` |
| 日志 | `%USERPROFILE%\.innervision\logs\app.log` |
| 本地备份 | `%USERPROFILE%\.innervision\backups` |

测试会用临时目录，不会改你的真实数据。也可以用环境变量 `INNERVISION_DATA_DIR` 指定其他本机文件夹。

## 项目结构

- `app.py`：Streamlit 入口。
- `src/innervision/services`：业务规则和授权核销。
- `src/innervision/mcp`：FastMCP 工具和进程内客户端。
- `src/innervision/agent`：意图识别、LangChain 只读工具循环、释放问句改写。
- `skills/*/SKILL.md`：感恩、愿景、释放、行动、仪式和每周报告的技能文本，只作为提示，不会被执行。

写入分三步：提出请求、用户确认、业务服务核销令牌。模型拿到的工具只有只读查询。安全停止不会把风险原文交给改写模型，也不会把日记正文写进日志。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 语音

语音不是默认安装的一部分。安装 `requirements-voice.txt` 之后，感恩日记、每日仪式和对话页会出现录音控件。转写使用本机的 faster-whisper，录音不会上传。未安装时，这些页面只提示它可以稍后启用。

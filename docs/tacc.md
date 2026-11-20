# TACC SDK 模型连接

模型为课程公共环境中的 Qwen3.8-27B-AWQ-INT4，服务名为 `qwen3.8-27b`。Windows 通过 WSL 中已配置的 `tcloud` 提交作业。课程允许本地通过 SDK 启动集群内客户端，客户端再访问模型的内网 API。

1. 双击 `Start-TACC.cmd`，等到显示 `TACC Qwen ready`。启动和排队可能需要数分钟。已有可用服务时会复用。
2. 双击 `Start.cmd` 启动工作台。模型设置为 `MODEL_MODE=tacc`、`TACC_PROTOCOL=sdk`、`TACC_MODEL=qwen3.8-27b`。可以保留 `APP_DEMO=1` 使用本地账号和模拟邮箱，模型本身仍在 TACC 上真实执行。
3. 上传课程允许使用的合成或脱敏合同。每份审查提交一个 CPU 客户端作业，完成后从 TACC 读取结构化结果。审查原件留在本机，解析文本和审查要求会传入 TACC。远端结果存于个人 `USERDIR/clause-results`。
4. 使用完毕双击 `Stop-TACC.cmd`。本项目服务最长运行一小时。下次演示重新启动服务时会刷新节点地址。

服务使用作业独立端口，避免与共享节点的默认端口冲突。启动沿用调度器分配的 GPU，使用 16K 上下文和较低并发以降低显存占用。

客户端提交会携带 `resolve_endpoint.py`，在进入课程运行环境前解析服务地址。课程节点名如 `10-0-1-10` 在计算节点上可能无 DNS 记录，此时转换为课程编码对应的私有 IPv4 地址，保留作业独立端口。普通未知域名不会被猜测。解析失败与显存不足应分别记录。

中栏输入具体问题会生成「本次问题」结果。保存 Skill 时可以取消所有内置类别，只填模板 Prompt 或添加命名的自定义项。模型会给出回答和可定位的原文证据。已有 Skill 的额外关注点自动作为独立专项要求执行。

运行 `python scripts/check_connections.py --live` 会排队提交一条合成探测。日常查看状态可运行 `python scripts/tacc_service.py status`。服务编号与地址保存在运行数据目录的 `tacc-service.json`。作业恢复会复用已记录的客户端编号。

账号首次使用时需要按课程提供的 `STUDENT-COMMANDS.md` 配置官方 SDK 和公共环境，该课程文件不在本工程包内。新电脑需要使用自己的授权账号配置 SDK，TACC 的内网 HTTP 地址仅供集群内客户端使用。当前可复现配置以根目录 README.md 为准。

参考：[课程使用的官方 tcloud SDK](https://github.com/turingaicloud/tcloud-sdk)。

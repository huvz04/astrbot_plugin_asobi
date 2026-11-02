# ASOBI Webhook 通知插件

将 [Asb7k-tools](https://github.com/tomyuan520/Asb7k-tools) 已有监控服务产生的 Webhook，转发到指定的 AstrBot 会话。插件需要 AstrBot `>=4.16,<5`，使用独立的 aiohttp HTTP 服务，默认接收地址为 `http://127.0.0.1:8766/asobi/webhook`。

检测工具继续负责登录、每天校准、到开奖时间查询和识别结果。AstrBot 插件持续监听请求，将通知持久化入队后立即确认接收，再独立生成图卡、发送和重试，不查询 ASOBI，也不重复安排开奖定时任务。检测工具只对尚未确认接收的 Webhook 重试。

结果图卡按同一 Live、同一开奖批次汇总多个账号，使用白底和 Live 标题，按演出日期 D1 / D2 / D3 及明确的开演时间分组。每条申请显示席位、账号及矩形状态块，账号中间用 `***` 打码；左侧红色竖线在落选时改为灰色。已入金、待入金使用红底白字，待抽选使用红色描边，落选使用灰色。每组依次显示已入金、待入金、其他状态和落选，不使用统计块或装饰底色。使用 AstrBot 内置 `html_render` 渲染，账号在传入渲染系统前打码；渲染失败或超过 30 秒时改发打码文字。查询故障也使用打码文字发送。

D 编号优先采用结果中的明确 DAY 标记，否则按已识别的演出日期顺序编号。同日有明确 `START` / `開演` 时间时进一步分场，不从开场、申请或开奖时间推测开演时间；无法识别日期时单列为待确认。同一申请包含多个演出日期或多个开演时间时单列为多场申请，不将一次中选复制到多个日期或场次。

席位优先从 `display_detail` 中提取，兼容旧版的 `detail`。多志愿申请列出全部志愿席位并注明「志愿席位」，不把申请席位当作已确定的中奖志愿；没有席位信息时如实提示。新版图卡和主动查询测试需同时更新检测工具与插件至 `v1.3.0` 对应版本。

**所有配置的收件人都会收到全部监控账号的通知。** 插件不按账号邮箱分流；需要隔离不同人的账号信息时，应使用独立的检测配置和插件部署。

## 安装

在 AstrBot 插件管理页选择通过仓库链接安装，填写：

```text
https://github.com/huvz04/astrbot_plugin_asobi
```

这是插件的独立仓库，仓库根目录包含插件入口和配置文件。安装后可通过 AstrBot 插件管理页更新和重新加载。检测工具位于另一个仓库 [Asb7k-tools](https://github.com/tomyuan520/Asb7k-tools)，不要将检测工具仓库 URL 填入插件安装框。

也可下载本插件，将插件文件放到 AstrBot 的 `data/plugins/astrbot_plugin_asobi/` 下，使目录结构如下，再通过 AstrBot 插件管理页重新加载插件：

```text
AstrBot/
└─ data/
   └─ plugins/
      └─ astrbot_plugin_asobi/
         ├─ main.py
         ├─ receiver.py
         ├─ card.py
         ├─ card.html
         ├─ metadata.yaml
         ├─ _conf_schema.json
         └─ requirements.txt
```

也可以从 [Releases](https://github.com/huvz04/astrbot_plugin_asobi/releases) 下载插件 ZIP，使用 AstrBot 的 ZIP 插件安装入口。ZIP 内应以 `astrbot_plugin_asobi/` 为唯一一级目录，内含全部插件文件，不能上传整个 Asb7k-tools 源码包。安装环境需能安装插件 `requirements.txt` 中的 `aiohttp>=3.9,<4`；它不需要检测工具的 Playwright 等依赖。

使用完整结果图卡时须同时更新 Asb7k-tools 检测工具和本插件至本版：只更新插件无法显示旧工具未发送的落选或其他账号结果。旧版 `won` 事件仍可接收。

## 配置 AstrBot

在插件管理页中填写配置，保存后重新加载插件：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `host` | `127.0.0.1` | HTTP 监听地址；跨主机或容器接入时参阅下方部署说明。 |
| `port` | `8766` | 独立 HTTP 端口，不能与其他服务冲突。 |
| `path` | `/asobi/webhook` | 接收 POST 的 URL 路径。 |
| `token` | 空 | 可选共享密钥；留空即可监听且不校验请求头。填写时须至少 16 个可见 ASCII 字符，不能包含空白。 |
| `recipients` | `[]` | 接收通知的完整 UMO 列表，支持 AstrBot 会话选择器。无效项会被忽略，合法项继续使用；无有效收件人也返回 `202` 并跳过发送，后续绑定不补发旧通知。 |

本地使用可将 `token` 保持为空，配置收件会话后即可向对应会话发送新通知。每条通知使用接收时的收件人列表；尚未配置收件人时正常跳过发送，配置并重载插件后不补发旧通知。需要鉴权时，可随机生成密钥，例如在有 Python 的环境执行：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

在期望收到通知的私聊或群聊中发送：

```text
/asobi会话
```

将命令返回的完整会话 UMO 填入 `recipients`，或用配置页的会话选择器选择。请直接复制命令结果，不能只填写 QQ 号或自行拼接 UMO。此命令任何用户都能使用，只展示当前会话，**不会自动订阅**。指定用户私聊接收时，就在与该用户对应的私聊会话获取 UMO。

插件会去除 UMO 两端空白并校验格式，忽略裸 QQ 号、非字符串等无效项，不会据此猜测平台或拼接 UMO，也不会因此加载失败。`recipients` 本身不是列表时整组忽略；全部无效时按零收件人处理。配置问题会显示在 `/asobi状态` 中，AstrBot 日志只记录固定提醒和列表项序号，不输出具体会话 ID。

任何用户都可以在普通会话发送以下命令，查看是否监听成功、监听地址、有效收件人数、配置提醒、待发事件数及最近错误：

```text
/asobi状态
```

两个诊断命令都无需管理员权限。状态命令不展示密钥、事件 ID、账号或收件会话名单；最近错误只显示固定说明。每个目标平台仍需连接正常，并具备向该私聊或群聊主动发消息的能力。默认配置会启动本地监听，收件人数为 `0` 不影响接收通知。

## 配置 Asb7k-tools

### GUI 配置

在原工具中打开「通知与后台监控」，启用 Webhook：

1. 接收地址填写 `http://127.0.0.1:8766/asobi/webhook`，端口和路径以 `/asobi状态` 显示的实际配置为准；例如插件监听 `8788` 时应填写 `http://127.0.0.1:8788/asobi/webhook`。跨主机时替换为检测工具实际可访问的地址。
2. 本地使用将「请求头」留空。若在插件中填写了 `token`，则在此填写 `Authorization: Bearer 相同密钥`，每行一个请求头，使用纯文本而非 JSON。
3. 点击「发送测试通知」。检测工具会按当前表单选择的账号主动查询一次，再将真实结果按同一 Live、同一开奖批次分别发送；每次测试使用新事件 ID。工具提示成功只表示 Webhook 已接收，实际图卡请到指定会话检查。
4. 点击「保存并启用 / 重启」，使原工具的后台检测生效。

原工具仍需先完成 ASOBI 账号登录。插件不保存 ASOBI 密码，也不处理会话过期后的重新登录。

### 手动配置

将下列内容保存到原工具的 `data/service.json`：

```json
{
  "accounts": [],
  "use_environment": false,
  "webhook": {
    "url": "http://127.0.0.1:8766/asobi/webhook",
    "headers": {}
  }
}
```

`accounts` 留空表示监控所有已保存账号；可填写邮箱或账号代号缩小检测范围。`use_environment: false` 可避免环境变量覆盖接收地址。若同时配置邮件通知，保留原有 `email` 配置即可。若插件设置了 `token`，将 `headers` 改为 `{"Authorization": "Bearer 相同密钥"}`。

在 Asb7k-tools 项目根目录执行：

```powershell
# 仅检查配置，不发送消息
.\.venv\Scripts\python.exe -m app serve --config .\data\service.json --check-config

# 主动查询配置中的账号一次，并按 Live / 开奖批次发送真实结果
.\.venv\Scripts\python.exe -m app serve --config .\data\service.json --test-notification

# 启动原工具的持续检测
.\.venv\Scripts\python.exe -m app serve --config .\data\service.json
```

Linux / macOS 将 Python 路径替换为 `./.venv/bin/python`。命令行与 GUI 使用相同的主动查询流程，`accounts` 为空时查询全部已保存账号，按 Live / 开奖批次发送本次查询结果。GUI 使用当前草稿中的账号和渠道设置，不自动保存配置。常驻运行和开机启动请沿用原工具的 [后台部署说明](https://github.com/tomyuan520/Asb7k-tools/blob/main/deploy/README.md)。

部分账号查询失败时仍发送成功账号的结果，并报告失败数量；命令行同时返回失败退出码。没有账号、全部查询失败或没有申请记录时明确提示，不发送旧缓存或样例。主动查询会正常更新账号查询缓存，但不修改正式监控的去重记录、待发队列或开奖计划。

## 本地、远程和 Docker 部署

### 同一台主机

两端直接运行在同一操作系统中时，可保留 `host=127.0.0.1`、`token` 为空，接收 URL 使用 `http://127.0.0.1:8766/asobi/webhook`，请求头留空。此端口属于插件，不是 AstrBot WebUI 端口。

### 不同主机

将插件的 `host` 改为 AstrBot 主机可接入的网卡地址，或 `0.0.0.0`，然后允许检测工具访问该端口。`0.0.0.0` 是监听配置，不能作为检测工具的目标地址；URL 应使用 AstrBot 主机的实际 IP 或域名。

远程接入如需鉴权，可填写 `token`，并在检测工具添加对应的 `Authorization: Bearer 相同密钥` 请求头。跨公网建议通过 HTTPS 反向代理接入；HTTP 会明文传输账号通知及已配置的共享密钥，防火墙可只允许检测工具所在主机访问。

### AstrBot 运行在 Docker 中

插件的 `host` 需要设置为 `0.0.0.0`，让容器端口能接收入站连接。在 AstrBot 原有 Compose 服务中增加端口映射，例如检测工具运行在 Docker 宿主机时：

```yaml
services:
  astrbot:
    # 保留现有 image、volumes 等配置
    ports:
      - "127.0.0.1:8766:8766"
```

保留现有其他端口映射，并在修改 Compose 后重新创建容器。此例只向宿主机本地发布插件端口，原工具使用 `http://127.0.0.1:8766/asobi/webhook`。

若检测工具位于远程主机，可将映射改为 `8766:8766` 并配合防火墙或 HTTPS 反向代理。若检测工具也在同一个 Docker 网络中，应使用 AstrBot 的容器服务名，例如 `http://astrbot:8766/asobi/webhook`；一个容器中的 `127.0.0.1` 不会指向另一个容器。

持久化 AstrBot 的 `data` 目录，以保留收件人配置、待发队列和发送去重记录。插件数据库位置为 `StarTools.get_data_dir("astrbot_plugin_asobi") / "deliveries.sqlite3"`，通常位于 AstrBot 的 `data/plugin_data/astrbot_plugin_asobi/` 下。

### HTTPS 反向代理

反向代理应将接收路径转发到插件 HTTP 端口，保留 POST 正文；启用可选密钥鉴权时也需保留 `Authorization` 请求头。例如同机 Nginx 的 HTTPS 站点中可加入：

```nginx
location = /asobi/webhook {
    client_max_body_size 1m;
    proxy_pass http://127.0.0.1:8766;
    proxy_set_header Authorization $http_authorization;
    proxy_read_timeout 15s;
}
```

证书和域名按现有站点配置。原工具的 URL 对应改为 `https://你的域名/asobi/webhook`。如果 Nginx 也在容器中，应把上游地址替换为它可访问的 AstrBot 容器地址。

## 接口与送达行为

通知请求为 `POST <path>`。默认无需鉴权；仅在插件配置非空 `token` 时，头部才必须包含 `Authorization: Bearer <token>`。正文为原工具的 JSON 事件，最大 1 MiB：

```json
{
  "id": "由原工具生成的稳定事件标识",
  "type": "result",
  "account": "",
  "title": "示例 LIVE · 抽选结果",
  "message": "同一 Live、同一开奖批次的结果汇总",
  "batch": {
    "title": "示例 LIVE",
    "lottery_at": "2026年11月3日 13:00",
    "lottery_at_iso": "2026-11-03T04:00:00Z"
  },
  "entries": [],
  "results": [
    {
      "account": "me@example.com",
      "title": "示例 LIVE",
      "status": "落選",
      "category": "落選",
      "detail": "2026 / 12 / 4 D1",
      "display_detail": "2026 / 12 / 4 D1\nOPEN 15:30 / START 17:00\n一般指定席 2枚",
      "lottery_at": "2026年11月3日 13:00",
      "lottery_at_iso": "2026-11-03T04:00:00Z",
      "cached": false
    }
  ],
  "created_at": "2026-11-03T04:05:00+00:00"
}
```

支持 `won`（含新中选的结果）、`result`（无新中选的已开奖结果，如纯落选）、`error`（连续查询失败）和 `test`（测试）事件。新结果事件的 `batch` 包含 Live 标题和开奖时间，`results` 每条包含账号及申请的安全展示字段；`display_detail` 保留完整的演出及志愿席位文本，旧版 `detail` 保持兼容。`cached: true` 表示来自该账号最近一次成功查询，并在图卡中标明。主动查询测试使用 `type: "test"`、`test_source: "live"`，图卡标注「主动查询」，仅包含本次查询结果，所有条目的 `cached` 均为 `false`。`result` 和 `test` 的顶层 `account` 可以为空。仍兼容仅有 `entries` 的旧版 `won` 事件。Webhook 及本地持久化数据仍含账号邮箱，打码发生在生成图卡和发送文字之前，不包含密码、Cookie 或登录 token。

检测工具从旧版本升级时，会一次性将已有监控账号旧缓存中的落选设为历史基线，避免更新后集中推送旧批次；旧待开奖条目不会被记作已通知，之后新查到落选仍会正常推送。已有中选通知记录继续沿用。

正式监控会为同一 Live、同一开奖批次补齐各监控账号的结果；同轮尚未尝试发送的候选合并为一条通知，纯落选也会通知。其他账号使用最近一次成功查询的缓存时会标记，并不会为图卡额外查询账号。开奖调度保持不变，付钱状态改变本身不会触发重复通知。

浏览器打开同一地址会发送 GET；GET 和 HEAD 均返回 `200`，仅说明 HTTP 入口可访问，不校验密钥，也不发送通知。使用原工具的「发送测试通知」或 `--test-notification` 验证接收，再到指定会话确认实际送达。其他方法仍返回 `405`，错误路径返回 `404`。下表为 POST 通知请求的状态：

| HTTP 状态 | 含义 |
| --- | --- |
| `202` | 通知已持久化接收，或相同事件 ID 已接收；不表示目标会话已收到消息。 |
| `400` | JSON 或必要事件字段无效。 |
| `401` | 已启用密钥鉴权，但请求头中的共享密钥缺失或不匹配。 |
| `413` | 请求正文超过 1 MiB。 |
| `503` | 暂时无法持久化接收请求，具体原因见响应 JSON 的 `error` 字段；检测工具可重试。 |

插件在 SQLite 中保存通知后返回 `202`，响应格式为 `{"ok":true,"status":"accepted","id":"事件标识"}`。相同 ID 的重复请求也返回 `202`，不会重复入队。只有网络不可达、鉴权或正文校验失败、数据库不可用等尚未接收的情况会让检测工具看到错误；数据库保存失败返回 `503`，不会将未保存的请求报告为成功。

后台任务独立生成图卡并发送队列中的通知，每条事件在每次发送尝试中统一准备一次消息，供尚未送达的目标共用；图卡渲染最多等待 30 秒，不占用每个目标的 10 秒发送超时。事件发送失败后由 AstrBot 插件每隔 60 秒只重试尚未成功的目标。接收时没有有效收件人则正常跳过发送，仍返回 `202`；QQ 等平台离线、平台无法识别已通过格式校验的会话或发送失败也只影响 AstrBot 内部发送状态，不会改变已经返回的接收成功。通过 `/asobi状态` 的待发事件数和最近错误、以及 AstrBot 日志排查平台发送问题。

插件在接收时固定每条通知的收件人列表，后续新增收件人只接收新事件，不补发旧通知；空收件人事件同样保留事件 ID 去重，重复投递不会触发补发。插件按 `(事件 id, 收件会话 UMO)` 保存发送成功记录，部分目标失败时只重试尚未成功的目标；正常重载后待发队列和成功记录均保留，也兼容旧版发送去重记录。从 `v1.1.0` 升级时，尚未分配收件人的旧待发事件会标记为已处理，不再补发，已有固定目标的待发事件继续重试。插件重载或卸载时会释放 HTTP 监听端口。

检测工具的普通 `serve` 任务仍会保留尚未确认接收的通知，并在 1 小时后以相同 `id` 重试；`--test-notification` 会即时查询并发送，请求失败后需要手动再次执行，届时会重新查询并使用新事件 ID。收到 `202` 后，后续平台发送和重试由 AstrBot 负责。

发送平台已接收消息、但进程在保存成功记录前中断等极端情况仍可能造成重复通知，无法保证严格的恰好一次送达。不要删除插件数据库或原工具的 `data/monitor.sqlite3`，否则已有去重记录会丢失。

## 验证与排查

1. 确认 AstrBot 平台适配器在线，插件已启用。
2. 在目标私聊或群聊执行 `/asobi会话`，将返回 UMO 加到 `recipients`，保存并重新加载。本地使用可保持 `token` 为空。收件人尚未配置时，通知也会正常接收，但跳过发送且不补发。
3. 执行 `/asobi状态`，检查监听成功、有效收件人数、配置提醒、待发事件数和最近错误。有配置提醒时，按日志中的项序号替换错误值，保存并重载；无需改动合法收件人。
4. 可先在浏览器打开接收地址，检查 HTTP 入口可访问；这不代表通知已接收或送达。再用原工具 GUI 的「发送测试通知」或上述 `--test-notification` 命令验证接收，最后在指定会话确认实际送达。
5. 重新加载插件后再测试，确认 HTTP 端口被正常释放并重新绑定；持续检测由原工具后台任务负责。

连接被拒绝时，检查插件日志是否提示端口冲突或配置无效，以及监听地址、Docker 映射和防火墙。`401` 表示已启用密钥鉴权，检查两端密钥和 `Bearer ` 前缀。原工具不跟随 HTTP 重定向，URL 应直接指向最终接收地址。

发送测试通知返回 `503` 表示插件尚未接收请求，应检查 AstrBot 日志、数据库目录是否可写及插件是否正在重载。新版不会因收件人为 `0` 或 QQ 发送失败返回 `503`。测试通知成功但 QQ 未收到时，执行 `/asobi状态`：收件人数为 `0` 就配置 `recipients` 并重载，再发送一条新的测试通知；有收件人则结合最近错误和 AstrBot 日志检查平台连接、UMO 和发送权限。浏览器 GET 成功只代表 HTTP 入口可访问。

若命令无回复，先尝试 `/asobi会话`；它也无回复时，检查插件是否加载成功并已启用、当前 AstrBot 配置文件是否允许此插件，以及命令所需的唤醒前缀。命令无回复本身不能确定是插件加载问题。

`405 Method Not Allowed` 表示该地址不接受当前请求方法。旧版插件仅接受 POST，浏览器直接打开会收到 `405`；新版 GET/HEAD 只检查入口。若原工具发送测试通知时仍收到 `405`，检查 URL 是否指向插件独立端口（默认 `8766`，不是 AstrBot WebUI 端口）、路径是否与插件配置一致，以及反向代理是否把 POST 转发到插件而非静态页面。

本插件的离线测试不能替代实际平台联调；部署后仍需使用自己的 AstrBot 版本、平台适配器和真实目标会话完成上述测试通知验证。

## 官方参考

- [AstrBot：消息的发送与会话 UMO](https://docs.astrbot.app/dev/star/guides/send-message.html)
- [AstrBot v4.16.0：内置 HTML 渲染接口](https://github.com/AstrBotDevs/AstrBot/blob/v4.16.0/astrbot/core/star/__init__.py)
- [AstrBot：插件配置](https://docs.astrbot.app/dev/star/guides/plugin-config.html)
- [AstrBot：插件存储](https://docs.astrbot.app/dev/star/guides/storage.html)

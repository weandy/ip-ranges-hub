# ip-ranges-hub 项目规范

每日自动更新的 CDN 与云服务商 IP 段聚合仓库。

## 结构约定

```
ip-ranges-hub/
├─ scripts/update.py        # 抓取+校验+聚合脚本（纯标准库，无第三方依赖）
├─ .github/workflows/update.yml
├─ lists/                   # 每厂商未聚合/聚合文件（自动生成，勿手改）
├─ cdn.txt                  # 方向 A：纯 CDN
├─ cdn_aws.txt              # CDN + AWS 全量 IP
├─ cloud.txt                # 方向 B：云服务商
├─ all.txt                  # 并集
└─ stats.json               # 各厂商段数元数据
```

- 生成产物（lists/、cdn.txt、cloud.txt、all.txt、stats.json）全部由 `update.py` 产出，**勿手改**。
- 代码改动只改 `scripts/update.py`，产物交给 CI 或本地脚本重新生成。

## 数据源约定

- 有官方源的厂商优先官方源，无官方源才用 RIPE ASN。
- 云厂商的 CDN 段（AWS CloudFront、Azure Front Door）必须按 service/tag 精确切，**不收全量**。
- 新增厂商 = 在 `update.py` 的 `providers` 元组加一行（name/kind/fetcher），无需改其他代码。

## 常用命令

- 本地生成全部产物：`python3 scripts/update.py`（需联网；每源失败不影响整体，退出码 1 仅当有源失败）
- 单源调试：`python3 -c "from scripts.update import *; print(len(fetch_cloudflare()))"`（需在项目根目录）

## 验证

- 改完脚本先本地跑 `python3 scripts/update.py`，确认 `cdn.txt`/`cloud.txt`/`all.txt` 行数合理（CDN 数千级、云服务商万级）。
- 检查失败源：脚本会打印 `✗ <厂商> 失败`，确认不是新增源被误杀。

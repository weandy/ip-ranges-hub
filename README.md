# ip-ranges-hub

每日自动更新的 **全球 CDN 与云服务商 IP 段聚合** 列表。四个产物：

- **`cdn.txt`** — 纯 CDN：CDN 厂商（Cloudflare/Akamai/Fastly/...）+ 云厂商的 CDN 服务（AWS CloudFront / Azure Front Door / Google CDN 近似）
- **`cdn_aws.txt`** — CDN + AWS 全量 IP（AWS 所有段，含 EC2/CloudFront/S3 等全部服务）
- **`cloud.txt`** — 云服务商：AWS / Azure / Google Cloud / Oracle / Linode / Vultr / OVH / Hetzner / Contabo / Scaleway 等全量段
- **`all.txt`** — 三者并集（聚合去重）

每个产物均有按协议拆分的 `_ipv4.txt` / `_ipv6.txt` 分文件。

产物由 GitHub Actions 每天 03:17 UTC 自动更新。

## 快速使用

从 GitHub 直接拉取（无需 clone 整个仓库）：

```bash
# 纯 CDN 段（最适合：排除 CDN 后定位真实 IP / 防火墙放行 CDN）
curl -sO https://raw.githubusercontent.com/weandy/ip-ranges-hub/main/cdn.txt
curl -sO https://raw.githubusercontent.com/weandy/ip-ranges-hub/main/cdn_ipv4.txt  # 仅 IPv4
curl -sO https://raw.githubusercontent.com/weandy/ip-ranges-hub/main/cdn_ipv6.txt  # 仅 IPv6

# CDN + AWS 全量（含 AWS 所有云段）
curl -sO https://raw.githubusercontent.com/weandy/ip-ranges-hub/main/cdn_aws.txt
curl -sO https://raw.githubusercontent.com/weandy/ip-ranges-hub/main/cdn_aws_ipv4.txt

# 云服务商全量段
curl -sO https://raw.githubusercontent.com/weandy/ip-ranges-hub/main/cloud.txt

# 并集
curl -sO https://raw.githubusercontent.com/weandy/ip-ranges-hub/main/all.txt
```

每行一个 CIDR，纯文本格式，可直接喂给 `iptables`、`nginx allow`、Go/Python 程序。

## 数据源

| 厂商 | 方向 | 数据源 | 说明 |
|---|---|---|---|
| Cloudflare | CDN | 官方 `cloudflare.com/ips-v4` + `ips-v6` | 官方纯文本 |
| Fastly | CDN | 官方 API `api.fastly.com/public-ip-list` | 官方 JSON |
| Gcore | CDN | 官方 API `api.gcore.com/cdn/public-ip-list` | 官方 JSON |
| Akamai | CDN | RIPE ASN × 38 | 官方无公开 CDN 列表，用完整 ASN 集合（排除 Linode） |
| Bunny / CDN77 | CDN | RIPE ASN | 无官方源 |
| AWS CloudFront | CDN | 官方 `ip-ranges.json` 按 `service=CLOUDFRONT` 等切 | 只收 CDN 服务段 |
| Azure Front Door | CDN | 官方 Service Tags 按 `AzureFrontDoor` tag 切 | 只收 CDN 服务段 |
| Google CDN | CDN | RIPE `AS15169` 近似 | 官方 cloud.json 切不出 CDN 细分 |
| AWS | 云 | 官方 `ip-ranges.json` 全量 | |
| Azure | 云 | 官方 Service Tags 全量 | 动态解析下载链接 |
| Google Cloud | 云 | 官方 `gstatic.com/ipranges/cloud.json` | |
| Oracle | 云 | RIPE ASN × 4 | 官方端点重定向成环，改用 RIPE |
| Vultr / Constant | 云 | RIPE `AS20473` | 同属 The Constant Company，共用 ASN |
| Cogent | 云 | RIPE `AS174` | 一级骨干 ISP，非纯 CDN |
| Datacamp | 云 | RIPE `AS212238` | 流媒体托管，非纯 CDN |
| OVH / Hetzner / Contabo / Scaleway | 云 | RIPE ASN | 无官方 JSON |

> **CDN 纯度**：本仓库的 `cdn.txt` 采用**严格模式**——只含纯 CDN 厂商 + 云厂商的 CDN 服务段。
> 骨干 ISP（Cogent）、流媒体托管（Datacamp）、VPS 商（Vultr/Constant）的段**不会**进 `cdn.txt`，
> 归入 `cloud.txt`。因为 `cdn.txt` 常被用作 zmap/扫描的排除列表，VPS/ISP 段上可能有人开代理，
> 绝不能误排除。

### 关于 Google CDN

Google 官方 `cloud.json` 的 `service` 字段目前只有统一的 `"Google Cloud"`，无法切出纯 CDN 段。
本仓库用 RIPE `AS15169`（Google 主 ASN）广播前缀近似。该段可能混入少量非 CDN 的 Google 段，属近似值。

### 关于 Akamai

Akamai 官方不提供公开 CDN IP 列表。本仓库采用 taythebot/cdn-ranges 整理的 **38 个 Akamai ASN**（39 个中排除 AS63949/Linode VPS 业务）的 RIPE 广播前缀，覆盖比单 AS20940 全面得多。

## 本地运行

```bash
cd scripts
python3 -m pip install -r requirements.txt  # 实际上脚本只用标准库，无依赖
python3 update.py
```

输出到 `lists/<厂商>.txt`、`lists/<厂商>_aggregated.txt`，聚合产物 `cdn.txt` / `cloud.txt` / `all.txt`。

## 目录结构

```
ip-ranges-hub/
├─ .github/workflows/update.yml   # 每日 cron + 手动触发
├─ scripts/update.py              # 抓取+校验+聚合脚本（纯标准库）
├─ lists/                         # 每厂商未聚合/聚合文件
├─ cdn.txt / cdn_ipv4.txt / cdn_ipv6.txt   # 纯 CDN 聚合（方向 A）
├─ cdn_aws.txt / cdn_aws_ipv4.txt          # CDN + AWS 全量
├─ cloud.txt / cloud_ipv4.txt              # 云服务商聚合（方向 B）
├─ all.txt / all_ipv4.txt / all_ipv6.txt   # 并集
└─ stats.json                     # 各厂商段数
```

## 参考项目

数据源与实现参考了：
- [123jjck/cdn-ip-ranges](https://github.com/123jjck/cdn-ip-ranges) — 每日更新的 CDN/代理 IP 聚合
- [taythebot/cdn-ranges](https://github.com/taythebot/cdn-ranges) — CDN 厂商 IP 下载（Akamai 完整 ASN 来源）
- [lord-alfred/ipranges](https://github.com/lord-alfred/ipranges) — 云厂商 IP 聚合
- [rezmoss/cloud-provider-ip-addresses](https://github.com/rezmoss/cloud-provider-ip-addresses) — 云服务商 IP 多格式导出

## 许可

MIT

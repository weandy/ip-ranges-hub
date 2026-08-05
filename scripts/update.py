#!/usr/bin/env python3
"""ip-ranges-hub：聚合 CDN 厂商与云服务商 IP 段，每日更新。

数据源策略：
- 有官方源的厂商：直接拉官方 JSON/纯文本（Cloudflare、Fastly、Gcore、AWS、Azure、Google Cloud、Oracle）
- 无官方源的 CDN 厂商：用 RIPE ASN 广播前缀（Akamai、Bunny、CDN77、Cogent 等）
- AWS/Azure：从官方源按 service/tag 精确切出 CDN 段（CloudFront / AzureFrontDoor）

产物：
- lists/<vendor>.txt        每个厂商一份（未聚合）
- lists/<vendor>_aggregated.txt  每个厂商聚合后
- cdn.txt      方向 A：纯 CDN（CDN 厂商 + 云厂商 CDN 服务）
- cloud.txt    方向 B：全部云服务商
- all.txt      两方向并集
"""
from __future__ import annotations

import ipaddress
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple, Union

# Windows 终端默认 GBK，强制 UTF-8 避免中文/符号编码错误
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 常量与数据源
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
LISTS_DIR = REPO_ROOT / "lists"
UA = "ip-ranges-hub-updater/1.0 (+github.com/ip-ranges-hub)"

# 官方数据源
CLOUDFLARE_V4 = "https://www.cloudflare.com/ips-v4"
CLOUDFLARE_V6 = "https://www.cloudflare.com/ips-v6"
FASTLY_IPS = "https://api.fastly.com/public-ip-list"
GCORE_IPS = "https://api.gcore.com/cdn/public-ip-list"
AWS_IPS = "https://ip-ranges.amazonaws.com/ip-ranges.json"
GOOGLE_IPS = "https://www.gstatic.com/ipranges/cloud.json"
# RIPE 广播前缀（无官方源的 CDN 厂商 / 兜底）
RIPE_URL = "https://stat.ripe.net/data/announced-prefixes/data.json?resource={asn}"

# Akamai：官方无公开 CDN 列表，用完整 ASN 集合（taythebot/cdn-ranges 整理）。
# 注意：AS63949 (AKAMAI-LINODE) 是 Linode VPS 业务，非纯 CDN，故排除。
AKAMAI_ASNS = [12222, 16625, 16702, 17204, 18680, 18717, 20189, 20940, 21342,
               21357, 21399, 22207, 22452, 23454, 23455, 23903, 24319, 26008,
               30675, 31107, 31108, 31109, 31110, 31377, 33047, 33905, 34164,
               34850, 35204, 35993, 35994, 36183, 39836, 43639, 55409, 55770,
               133103, 393560]

# 其他 CDN 厂商的 ASN（RIPE 数据源）
BUNNY_ASN = "200325"
CDN77_ASN = "60068"
# 骨干ISP / 流媒体托管 / VPS（方向 B，RIPE 数据源）
COGENT_ASN = "174"
DATACAMP_ASN = "212238"
# 云服务商 ASN（方向 B，RIPE 兜底）
VULTR_ASN = "20473"     # Vultr + Constant 同属 The Constant Company，共用 AS20473
OVH_ASN = "16276"
HETZNER_ASN = "24940"
CONTAVO_ASN = "51167"
SCALEWAY_ASN = "12876"

# Azure Service Tags 下载（带日期戳，见 _resolve_azure_url 的解析逻辑）
AZURE_CONFIRM_URL = "https://www.microsoft.com/en-us/download/confirmation.aspx?id=56519"
# 内置已知最新日期戳（兜底；优先走解析，解析失败才用）
AZURE_FALLBACK_FILE = "ServiceTags_Public_20260428.json"
AZURE_FALLBACK_URL = "https://download.microsoft.com/download/7/1/D/71D86715-5596-4529-9B13-DE13D8087D74/" + AZURE_FALLBACK_FILE

# AWS CDN 相关 service（切出 CloudFront 段）
AWS_CDN_SERVICES = {"CLOUDFRONT", "CLOUDFRONT_ORIGIN_FACING", "GLOBALACCELERATOR"}

# CloudFront 已知段（AWS 文档 LocationsOfEdgeServers 列出）。AWS ip-ranges.json 的
# service 字段不总是把这些标为 CLOUDFRONT（如 3.0.0.0/15 标为 AMAZON），
# 光靠 service 切会漏。这里补全 CloudFront 官方文档列出的已知段。
CLOUDFRONT_KNOWN = [
    "3.0.0.0/15", "13.32.0.0/15", "13.224.0.0/14", "13.226.0.0/15",
    "13.248.0.0/14", "52.84.0.0/15", "52.124.128.0/17", "52.221.96.0/22",
    "54.182.0.0/16", "54.192.0.0/16", "54.230.0.0/16", "54.233.128.0/17",
    "54.239.128.0/18", "54.239.192.0/19", "65.8.0.0/16", "65.9.0.0/16",
    "99.84.0.0/16", "130.176.0.0/16", "143.204.0.0/16", "144.220.0.0/16",
    "205.251.192.0/19", "205.251.224.0/19", "2600:9000::/33",
]

# Google：官方 cloud.json 只有统一 "Google Cloud" service，切不出纯 CDN。
# 用 RIPE AS15169（Google 主 ASN）广播前缀兜底，README 注明为近似。
GOOGLE_MAIN_ASN = "15169"


# ---------------------------------------------------------------------------
# 基础设施：HTTP 抓取（带重试）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Prefix:
    cidr: str
    region: str = ""


@dataclass(frozen=True)
class Provider:
    name: str
    kind: str            # "cdn" | "cloud"
    fetcher: Callable[[], Sequence[Prefix]]
    allow_empty: bool = False


class FetchError(RuntimeError):
    pass


def _fetch(url: str, timeout: int = 60, attempts: int = 3, delay: float = 2.0) -> bytes:
    """抓取 URL，失败重试；最终失败抛 FetchError。"""
    last_err: Optional[Exception] = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            # 用 build_opener 显式支持重定向（默认 HTTPRedirectHandler 会跟 301）
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            time.sleep(delay * (i + 1))
    raise FetchError(f"抓取 {url} 失败: {last_err}")


def _fetch_json(url: str) -> dict:
    return json.loads(_fetch(url).decode("utf-8"))


def _fetch_text(url: str) -> str:
    return _fetch(url).decode("utf-8")


def fetch_ripe_prefixes(asn: Union[str, int]) -> Sequence[Prefix]:
    """RIPE Stat 广播前缀：该 ASN 在 BGP 路由表中宣称的前缀。"""
    url = RIPE_URL.format(asn=str(asn).upper() if str(asn).upper().startswith("AS") else f"AS{asn}")
    payload = _fetch_json(url)
    return [Prefix(p["prefix"]) for p in payload.get("data", {}).get("prefixes", []) if p.get("prefix")]


# ---------------------------------------------------------------------------
# 各厂商 fetcher
# ---------------------------------------------------------------------------
def fetch_cloudflare() -> Sequence[Prefix]:
    v4 = [l.strip() for l in _fetch_text(CLOUDFLARE_V4).splitlines() if l.strip()]
    v6 = [l.strip() for l in _fetch_text(CLOUDFLARE_V6).splitlines() if l.strip()]
    return [Prefix(p) for p in v4 + v6]


def fetch_fastly() -> Sequence[Prefix]:
    d = _fetch_json(FASTLY_IPS)
    return [Prefix(p) for p in d.get("addresses", []) + d.get("ipv6_addresses", [])]


def fetch_gcore() -> Sequence[Prefix]:
    d = _fetch_json(GCORE_IPS)
    return [Prefix(p) for p in d.get("addresses", []) + d.get("ipv6_addresses", [])]


def fetch_aws() -> Sequence[Prefix]:
    d = _fetch_json(AWS_IPS)
    out = []
    for e in d.get("prefixes", []):
        if e.get("ip_prefix"):
            out.append(Prefix(e["ip_prefix"], e.get("region", "")))
    for e in d.get("ipv6_prefixes", []):
        if e.get("ipv6_prefix"):
            out.append(Prefix(e["ipv6_prefix"], e.get("region", "")))
    return out


def fetch_aws_cdn() -> Sequence[Prefix]:
    """AWS 中 CDN 服务段 = service=CLOUDFRONT 等 切出的段 + CloudFront 官方已知段。

    AWS ip-ranges.json 的 service 字段不总是可靠（如 3.0.0.0/15 标为 AMAZON 而非
    CLOUDFRONT），光靠 service 切会漏 CloudFront 段，故用官方文档已知段补全。
    """
    d = _fetch_json(AWS_IPS)
    cdn_cidrs: set[str] = set(CLOUDFRONT_KNOWN)
    for e in d.get("prefixes", []) + d.get("ipv6_prefixes", []):
        if e.get("service") in AWS_CDN_SERVICES:
            cidr = e.get("ip_prefix") or e.get("ipv6_prefix")
            if cidr:
                cdn_cidrs.add(cidr)
    return [Prefix(c, "") for c in sorted(cdn_cidrs)]


def fetch_azure() -> Sequence[Prefix]:
    """Azure Service Tags：带日期戳，先解析下载页拿到真实 URL，失败用兜底。"""
    try:
        url = _resolve_azure_url()
    except FetchError:
        url = AZURE_FALLBACK_URL
    d = _fetch_json(url)
    out = []
    for e in d.get("values", []):
        for p in e.get("properties", {}).get("addressPrefixes", []):
            out.append(Prefix(p, e.get("name", "")))
    return out


def _resolve_azure_url() -> str:
    """从 Azure 下载确认页解析真实 ServiceTags 下载链接。"""
    try:
        html = _fetch(AZURE_CONFIRM_URL).decode("utf-8", errors="ignore")
    except FetchError:
        html = ""
    m = re.search(r'https://download\.microsoft\.com/download/[^"\'\s<>]+ServiceTags[^"\'\s<>]*\.json', html)
    if not m:
        raise FetchError("无法从下载页解析 Azure ServiceTags 链接")
    return m.group(0)


def fetch_azure_cdn() -> Sequence[Prefix]:
    """Azure 中仅 CDN 服务的段：AzureFrontDoor 服务 tag。"""
    return [p for p in fetch_azure() if "FrontDoor" in p.region or "frontdoor" in p.region.lower()]


def fetch_google() -> Sequence[Prefix]:
    d = _fetch_json(GOOGLE_IPS)
    out = []
    for e in d.get("prefixes", []):
        cidr = e.get("ipv4Prefix") or e.get("ipv6Prefix")
        if cidr:
            out.append(Prefix(cidr, e.get("service", "")))
    return out


def fetch_google_cdn() -> Sequence[Prefix]:
    """Google CDN：官方 cloud.json 切不出细分，用 RIPE AS15169 兜底（近似）。"""
    return fetch_ripe_prefixes(GOOGLE_MAIN_ASN)


def fetch_oracle() -> Sequence[Prefix]:
    """Oracle：官方 public_ip_ranges.json 端点当前重定向成环(302->301 互相跳)，
    无法用官方源。改用 RIPE 广播前缀（Oracle 主 ASN），近似官方全量段。"""
    # Oracle Cloud 相关 ASN（RIPE 可查）
    oracle_asns = ["31898", "6142", "20054", "54253"]
    out: List[Prefix] = []
    for asn in oracle_asns:
        try:
            out.extend(fetch_ripe_prefixes(asn))
        except Exception:
            continue  # 单 ASN 失败不影响整体
    return out


# ---------------------------------------------------------------------------
# 处理：校验 / 聚合 / 去重
# ---------------------------------------------------------------------------
def normalize_prefixes(prefixes: Iterable[Prefix]) -> List[Prefix]:
    """校验 CIDR 合法、去重、按网络地址排序。"""
    seen = set()
    out: List[Prefix] = []
    for p in prefixes:
        cidr = p.cidr.strip()
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue  # 非法 CIDR 丢弃
        key = str(net)
        if key in seen:
            continue
        seen.add(key)
        out.append(Prefix(key, p.region))
    out.sort(key=lambda p: (p.cidr))
    return out


def aggregate_prefixes(prefixes: Iterable[Prefix]) -> List[Prefix]:
    """聚合相邻 CIDR 为更粗粒度（collapse_addresses）。IPv4/IPv6 分开聚合。"""
    v4, v6 = [], []
    for p in prefixes:
        net = ipaddress.ip_network(p.cidr, strict=False)
        (v4 if net.version == 4 else v6).append(net)
    out: List[Prefix] = []
    for nets in (v4, v6):
        if nets:
            out.extend(Prefix(str(n), "") for n in ipaddress.collapse_addresses(nets))
    return out


def write_list(provider: str, prefixes: Sequence[Prefix], aggregated: bool = False) -> None:
    fname = f"{provider}.txt" if not aggregated else f"{provider}_aggregated.txt"
    path = LISTS_DIR / fname
    path.write_text("".join(f"{p.cidr}\n" for p in prefixes), encoding="utf-8")


def write_combined(name: str, prefixes: Sequence[Prefix]) -> None:
    (REPO_ROOT / f"{name}.txt").write_text("".join(f"{p.cidr}\n" for p in prefixes), encoding="utf-8")


def write_split(name: str, prefixes: Sequence[Prefix]) -> None:
    """按协议拆成两个文件：<name>_ipv4.txt / <name>_ipv6.txt。"""
    v4, v6 = [], []
    for p in prefixes:
        (v4 if ipaddress.ip_network(p.cidr).version == 4 else v6).append(p)
    (REPO_ROOT / f"{name}_ipv4.txt").write_text("".join(f"{p.cidr}\n" for p in v4), encoding="utf-8")
    (REPO_ROOT / f"{name}_ipv6.txt").write_text("".join(f"{p.cidr}\n" for p in v6), encoding="utf-8")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    providers: Sequence[Provider] = (
        # ---- 方向 A：纯 CDN ----
        Provider("cloudflare", "cdn", fetch_cloudflare),
        Provider("fastly", "cdn", fetch_fastly),
        Provider("gcore", "cdn", fetch_gcore),
        Provider("akamai", "cdn", lambda: [p for asn in AKAMAI_ASNS for p in fetch_ripe_prefixes(asn)]),
        Provider("bunny", "cdn", lambda: fetch_ripe_prefixes(BUNNY_ASN)),
        Provider("cdn77", "cdn", lambda: fetch_ripe_prefixes(CDN77_ASN)),
        Provider("cloudfront", "cdn", fetch_aws_cdn),
        Provider("azure_frontdoor", "cdn", fetch_azure_cdn),
        Provider("google_cdn", "cdn", fetch_google_cdn, allow_empty=False),
        # ---- 方向 B：云服务商 / 托管 / ISP ----
        Provider("aws", "cloud", fetch_aws),
        Provider("azure", "cloud", fetch_azure),
        Provider("google", "cloud", fetch_google),
        Provider("oracle", "cloud", fetch_oracle),
        Provider("vultr", "cloud", lambda: fetch_ripe_prefixes(VULTR_ASN)),
        Provider("cogent", "cloud", lambda: fetch_ripe_prefixes(COGENT_ASN)),
        Provider("datacamp", "cloud", lambda: fetch_ripe_prefixes(DATACAMP_ASN)),
        Provider("ovh", "cloud", lambda: fetch_ripe_prefixes(OVH_ASN)),
        Provider("hetzner", "cloud", lambda: fetch_ripe_prefixes(HETZNER_ASN)),
        Provider("contabo", "cloud", lambda: fetch_ripe_prefixes(CONTAVO_ASN)),
        Provider("scaleway", "cloud", lambda: fetch_ripe_prefixes(SCALEWAY_ASN)),
    )

    cdn_prefixes: List[Prefix] = []
    cloud_prefixes: List[Prefix] = []
    aws_extra: List[Prefix] = []  # AWS 全量段（用于 cdn_aws.txt）
    failed: List[str] = []
    all_raw: List[Tuple[str, Prefix]] = []

    for spec in providers:
        try:
            raw = list(spec.fetcher())
            if not raw and not spec.allow_empty:
                raise FetchError(f"{spec.name}: 抓取为空")
            norm = normalize_prefixes(raw)
            agg = aggregate_prefixes(norm)
            write_list(spec.name, norm)
            write_list(spec.name, agg, aggregated=True)
            all_raw.extend((spec.name, p) for p in norm)
            if spec.kind == "cdn":
                cdn_prefixes.extend(agg)
            else:
                cloud_prefixes.extend(agg)
            if spec.name == "aws":
                aws_extra = agg  # 保存 AWS 聚合段，供 cdn_aws.txt 使用
            print(f"  ✓ {spec.name:16s} 未聚合 {len(norm):6d} 聚合 {len(agg):6d}")
        except Exception as e:
            print(f"  ✗ {spec.name:16s} 失败: {e}", file=sys.stderr)
            failed.append(spec.name)

    # 聚合产物
    cdn_agg = aggregate_prefixes(normalize_prefixes(cdn_prefixes))
    cloud_agg = aggregate_prefixes(normalize_prefixes(cloud_prefixes))
    all_agg = aggregate_prefixes(normalize_prefixes(cdn_prefixes + cloud_prefixes))
    # cdn_aws.txt：纯 CDN + AWS 全量（云厂商 CDN 里也含 CloudFront，但 AWS 全量是超集）
    cdn_aws_agg = aggregate_prefixes(normalize_prefixes(cdn_prefixes + aws_extra))
    write_combined("cdn", cdn_agg)
    write_combined("cloud", cloud_agg)
    write_combined("all", all_agg)
    write_combined("cdn_aws", cdn_aws_agg)
    # 按协议分文件（ipv4 / ipv6）
    for name, agg in (("cdn", cdn_agg), ("cdn_aws", cdn_aws_agg),
                      ("cloud", cloud_agg), ("all", all_agg)):
        write_split(name, agg)

    # 统计
    print(f"\n=== 汇总 ===")
    print(f"cdn.txt       (纯 CDN): {len(cdn_agg)} 段")
    print(f"cdn_aws.txt   (CDN + AWS全量): {len(cdn_aws_agg)} 段")
    print(f"cloud.txt     (云服务商): {len(cloud_agg)} 段")
    print(f"all.txt       (并集): {len(all_agg)} 段")
    print(f"(各产物均有 _ipv4.txt / _ipv6.txt 分文件)")

    # 写入厂商行数元数据（供 README/CI 展示）
    meta = {name: len(normalize_prefixes([p for n, p in all_raw if n == name])) for name in sorted(set(n for n, _ in all_raw))}
    (REPO_ROOT / "stats.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    if failed:
        print(f"\n失败厂商 ({len(failed)}): {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

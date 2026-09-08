import os
import random
import socket
import time
import re
import requests
import concurrent.futures
from datetime import datetime, timedelta, timezone

# ==========================================
# 🎯 全局默认地区设置 (如果想要永久换地区，只改这里！)
# 支持多个地区，用逗号隔开，例如 "SJC,LAX,HKG,FRA,NRT"
# 💡 新手不知道有什么地区？可以直接填 "ALL"，系统会全区盲扫并自动创建所有能扫到的地区子域名！
# ==========================================
DEFAULT_REGIONS = "SJC,LAX,HKG,FRA,NRT"

# 🌐 主域名终极大汇总同步开关
# 设置为 "YES": 开启！将所有扫到的极品节点汇总推送到你的主域名（全球负载均衡）
# 设置为 "NO": 关闭！仅同步到各个地区子域名，不修改主域名的解析记录
SYNC_MAIN_DOMAIN = "NO"

# 🎯 扫描与同步数量设置
# 控制每个地区最终要同步几个 IP 到 Cloudflare DNS (默认 10 个)
SYNC_COUNT = 5
# 控制每次随机生成多少个 IP 去抽卡测速 (默认 2000 个)
SCAN_COUNT = 2000
# ==========================================

    # === Cloudflare IPv4 Ranges (IP段配置区) ===
    # 现在完全从根目录的 ip.txt 文件读取
def load_cf_cidrs(file_path="ip.txt"):
    if not os.path.exists(file_path):
        print(f"Error: 找不到 {file_path} 文件！请确保该文件存在并填写了需要扫描的 IP 段。")
        exit(1)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            cidrs = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        if not cidrs:
            print(f"Error: {file_path} 文件为空！请在里面填入需要扫描的网段 (CIDR)。")
            exit(1)
        return cidrs
    except Exception as e:
        print(f"Error: 读取 {file_path} 失败！错误信息: {e}")
        exit(1)

CF_CIDRS = load_cf_cidrs()
    # ==========================================

def generate_random_ip(hot_cidrs=None):
    # 如果有热点网段，并且掷骰子命中 50% 概率，就从热点网段里抽；否则从大网段抽
    for _ in range(10): # 避免死循环，最多重试 10 次
        try:
            if hot_cidrs and random.random() < 0.5:
                cidr = random.choice(hot_cidrs)
            else:
                cidr = random.choice(CF_CIDRS)
                
            if '/' in cidr:
                base_ip, prefix = cidr.split('/')
                prefix = int(prefix)
            else:
                base_ip = cidr
                prefix = 32
            
            parts = list(map(int, base_ip.split('.')))
            if len(parts) != 4:
                continue
                
            ip_long = (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]
            
            host_bits = 32 - prefix
            mask = (1 << host_bits) - 1
            random_host = random.randint(0, mask)
            
            final_ip_long = (ip_long & ~mask) | random_host
            
            p1 = (final_ip_long >> 24) & 255
            p2 = (final_ip_long >> 16) & 255
            p3 = (final_ip_long >> 8) & 255
            p4 = final_ip_long & 255
            
            return f"{p1}.{p2}.{p3}.{p4}"
        except Exception:
            continue
            
    return "1.1.1.1" # 兜底返回，防止崩溃

def test_ip(ip, check_api_url, timeout=5.0):
    start_time = time.time()
    try:
        url = f"{check_api_url}?proxyip={ip}"
        
        resp = requests.get(url, timeout=timeout).json()
        if resp.get("success") is True:
            connect_time = int((time.time() - start_time) * 1000)
            
            # 提取数据中心 (dataCenter)、colo 或 country，优先用 dataCenter
            colo = resp.get("dataCenter") or resp.get("colo") or resp.get("country") or "UNK"
            
            # 如果 API 返回了 latencyMs 或者 latency，优先用 API 测算的延迟，否则用整个请求的耗时
            latency = resp.get("latencyMs") or resp.get("tcpDuration") or connect_time
            
            return {"ip": ip, "latency": latency, "colo": colo}
    except Exception:
        pass
    return None

def sync_to_cloudflare(api_token, zone_id, target_domain, best_ips, cf_email):
    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": api_token,
        "Content-Type": "application/json"
    }
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A&name={target_domain}"
    
    print(f"Fetching existing DNS records for {target_domain}...")
    try:
        resp = requests.get(url, headers=headers).json()
        if not resp.get("success"):
            print("Failed to fetch DNS records:", resp)
            return False
        
        existing_records = resp.get("result", [])
        existing_map = {r["content"]: r["id"] for r in existing_records}
        desired_ips = [ip["ip"] for ip in best_ips]
        
        # 1. Delete records that are no longer in our best_ips list
        for ip_val, record_id in existing_map.items():
            if ip_val not in desired_ips:
                print(f"Deleting outdated IP: {ip_val}")
                del_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
                requests.delete(del_url, headers=headers)
                
        # 2. Add new IPs
        for ip_val in desired_ips:
            if ip_val not in existing_map:
                print(f"Adding new IP: {ip_val}")
                post_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
                data = {
                    "type": "A",
                    "name": target_domain,
                    "content": ip_val,
                    "ttl": 60,  # Auto/1 minute
                    "proxied": False
                }
                requests.post(post_url, headers=headers, json=data)
                
        print("Cloudflare DNS Sync completed successfully!")
        return True
    except Exception as e:
        print(f"Exception during Cloudflare sync: {e}")
        return False

def save_ips_to_file(best_ips):
    # Calculate Beijing Time (UTC+8)
    bj_time = datetime.now(timezone.utc) + timedelta(hours=8)
    time_str = bj_time.strftime("%Y-%m-%d %H:%M:%S")
    
    with open("ips-v4.txt", "w", encoding="utf-8") as f:
        # 写入纯 IP 和 地区备注，格式为 IP#地区
        # 很多代理/机场客户端使用 # 作为节点备注的分隔符
        for ip in best_ips:
            f.write(f"{ip['ip']}#{ip['colo']}\n")
            
    print("Successfully saved latest IPs to ips-v4.txt")

def main():
    api_token = os.environ.get("CF_API_TOKEN")
    zone_id = os.environ.get("CF_ZONE_ID")
    base_domain = os.environ.get("CF_TARGET_DOMAIN")
    cf_email = os.environ.get("CF_EMAIL")
    
    region_input = DEFAULT_REGIONS
    target_regions = [r.strip().upper() for r in region_input.split(",") if r.strip()]
    is_scan_all = "ALL" in target_regions
    
    if is_scan_all:
        print(f"Target Regions dynamically set to: ALL (Global Scan Mode)")
    else:
        print(f"Target Regions dynamically set to: {target_regions}")
    
    check_api_url = "https://proxyip.xxxxxxxx.nyc.mn/check"
    sync_count = SYNC_COUNT
    scan_count = SCAN_COUNT
    
    # === 从 ips-v4.txt 中提取历史优秀 IP 段 (/24) ===
    hot_cidrs = []
    if os.path.exists("ips-v4.txt"):
        try:
            with open("ips-v4.txt", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        ip_str = line.split("#")[0]
                        parts = ip_str.split(".")
                        if len(parts) == 4:
                            hot_cidrs.append(f"{parts[0]}.{parts[1]}.{parts[2]}.0/24")
            hot_cidrs = list(set(hot_cidrs))
            print(f"Loaded {len(hot_cidrs)} hot /24 subnets from ips-v4.txt for targeted scanning.")
        except Exception as e:
            pass
    
    can_sync = True
    if not all([api_token, zone_id, base_domain, cf_email]):
        print("Warning: Missing required environment variables (CF_API_TOKEN, CF_ZONE_ID, CF_TARGET_DOMAIN, CF_EMAIL).")
        print("DNS Synchronization will be skipped, but IP scanning will still proceed!")
        can_sync = False
        
    print(f"Generating {scan_count} random Cloudflare IPs...")
    ips_to_test = [generate_random_ip(hot_cidrs) for _ in range(scan_count)]
    
    print(f"Testing IPs concurrently via {check_api_url}...")
    
    valid_ips_by_region = {}
    if not is_scan_all:
        valid_ips_by_region = {region: [] for region in target_regions}
    
    # We will loop scanning until we find enough IPs for all regions, or hit max attempts.
    max_attempts = 10
    attempt = 0
    ALL_MODE_LIMIT = 20
    
    while attempt < max_attempts:
        # Check if we hit our target sync count for ALL target regions
        total_collected = sum(len(ips) for ips in valid_ips_by_region.values())
        if is_scan_all and total_collected >= ALL_MODE_LIMIT:
            break
        elif not is_scan_all and all(len(ips) >= sync_count for ips in valid_ips_by_region.values()):
            break
            
        attempt += 1
        print(f"--- Scan Iteration {attempt} ---")
        ips_to_test = [generate_random_ip(hot_cidrs) for _ in range(scan_count)]
        
        # === 并发线程配置区 ===
        # 控制同时发起多少个测速请求，默认 50，太高容易导致测速接口崩溃
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = {executor.submit(test_ip, ip, check_api_url): ip for ip in ips_to_test}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    colo = result.get('colo', 'UNK').upper()
                    if colo != 'UNK' and (is_scan_all or colo in target_regions):
                        if colo not in valid_ips_by_region:
                            valid_ips_by_region[colo] = []
                            
                        if is_scan_all:
                            total_collected = sum(len(ips) for ips in valid_ips_by_region.values())
                            if total_collected < ALL_MODE_LIMIT:
                                valid_ips_by_region[colo].append(result)
                                print(f"[FOUND {colo}] {result['ip']} (Total ALL: {total_collected + 1}/{ALL_MODE_LIMIT})")
                        else:
                            if len(valid_ips_by_region[colo]) < sync_count:
                                valid_ips_by_region[colo].append(result)
                                print(f"[FOUND {colo}] {result['ip']} (Total {colo}: {len(valid_ips_by_region[colo])}/{sync_count})")
                        
                # Early exit check
                total_collected = sum(len(ips) for ips in valid_ips_by_region.values())
                if is_scan_all and total_collected >= ALL_MODE_LIMIT:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                elif not is_scan_all and all(len(ips) >= sync_count for ips in valid_ips_by_region.values()):
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                    
    print("\nScan completed. Summary:")
    total_found = 0
    all_best_ips = []
    
    for region, ips in valid_ips_by_region.items():
        print(f"- {region}: {len(ips)} valid IPs found")
        if not ips:
            print(f"  Warning: No IPs found for {region}")
            continue
            
        total_found += len(ips)
        
        # Sort by latency (lowest first)
        ips.sort(key=lambda x: x["latency"])
        
        # Take the top fastest ones
        limit = ALL_MODE_LIMIT if is_scan_all else sync_count
        best_ips = ips[:limit]
        all_best_ips.extend(best_ips)
        
        print(f"\n--- Top {len(best_ips)} IPs Selected for {region} ---")
        for ip in best_ips:
            print(f"IP: {ip['ip']:<15} | Latency: {ip['latency']:>3}ms | Colo: {ip['colo']}")
            
        # Target domain specific to this region
        if can_sync:
            target_domain = f"{region.lower()}.{base_domain}"
            print(f"\nStarting Cloudflare DNS Sync for {target_domain}...")
            sync_to_cloudflare(api_token, zone_id, target_domain, best_ips, cf_email)
        else:
            print(f"\nSkipping Cloudflare DNS Sync for {region} (Missing Credentials).")
                
    if can_sync and all_best_ips:
        if SYNC_MAIN_DOMAIN.strip().upper() == "YES":
            all_best_ips.sort(key=lambda x: x["latency"])
            print(f"\n[Global Sync] Starting Cloudflare DNS Sync for MAIN DOMAIN: {base_domain}")
            sync_to_cloudflare(api_token, zone_id, base_domain, all_best_ips, cf_email)
        else:
            print(f"\n[Global Sync] Skipped synchronizing to MAIN DOMAIN ({base_domain}) because SYNC_MAIN_DOMAIN is set to NO.")

    if total_found == 0:
        print("No valid IPs found in this scan across any regions. Aborting.")
        exit(1)
        
    # Save ALL best IPs from all regions to the text file for next run's subnet learning
    if all_best_ips:
        save_ips_to_file(all_best_ips)

if __name__ == "__main__":
    main()

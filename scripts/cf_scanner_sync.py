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
DEFAULT_REGIONS = "sjc"

# 🌐 主域名终极大汇总同步开关
# 设置为 "YES": 开启！将所有扫到的极品节点汇总推送到你的主域名（全球负载均衡）
# 设置为 "NO": 关闭！仅同步到各个地区子域名，不修改主域名的解析记录
SYNC_MAIN_DOMAIN = "NO"

# 🎯 扫描与同步数量设置
# 控制每个地区最终要同步几个 IP 到 Cloudflare DNS (默认 10 个)
SYNC_COUNT = 20
# 控制 ALL 全局模式下，最终要扫出多少个 IP 才停止 (默认 200 个)
ALL_MODE_LIMIT = 10
# ========================================

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

def generate_random_ip_from_cidrs(cidrs):
    # 从指定的 CIDR 列表中随机抽取网段并生成一个随机 IP
    for _ in range(10): # 避免死循环，最多重试 10 次
        try:
            cidr = random.choice(cidrs)
                
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

    # 追加保存优质 IP 段到执行日志
    log_file = "hot_cidrs.txt"
    
    existing_cidrs = set()
    if os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    existing_cidrs.add(line)
    
    for ip in best_ips:
        parts = ip['ip'].split('.')
        if len(parts) == 4:
            cidr_str = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24#{ip['colo']}"
            existing_cidrs.add(cidr_str)
            
    with open(log_file, "w", encoding="utf-8") as f:
        for cidr in sorted(list(existing_cidrs)):
            f.write(f"{cidr}\n")
    print(f"Successfully saved hot CIDRs to {log_file}")

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
    
    check_api_url = "https://cloudflarenb-pagesip.pages.dev/check"
    sync_count = SYNC_COUNT
    all_mode_limit_count = ALL_MODE_LIMIT
    
    # === 1. 从 ips-v4.txt 提取历史优秀 IP ===
    historical_ips = []
    if os.path.exists("ips-v4.txt"):
        try:
            with open("ips-v4.txt", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        ip_str = line.split("#")[0]
                        historical_ips.append(ip_str)
            print(f"Loaded {len(historical_ips)} historical IPs from ips-v4.txt")
        except Exception as e:
            pass

    # === 2. 从执行日志提取对应地区的优质 IP 段 ===
    hot_cidrs = []
    log_file = "hot_cidrs.txt"
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and "#" in line:
                        cidr, colo = line.split("#", 1)
                        if is_scan_all or colo.upper() in target_regions:
                            hot_cidrs.append(cidr)
            hot_cidrs = list(set(hot_cidrs))
            print(f"Loaded {len(hot_cidrs)} targeted hot /24 subnets from {log_file}")
        except Exception as e:
            pass
    
    can_sync = True
    if not all([api_token, zone_id, base_domain, cf_email]):
        print("Warning: Missing required environment variables (CF_API_TOKEN, CF_ZONE_ID, CF_TARGET_DOMAIN, CF_EMAIL).")
        print("DNS Synchronization will be skipped, but IP scanning will still proceed!")
        can_sync = False
        
    valid_ips_by_region = {}
    if not is_scan_all:
        valid_ips_by_region = {region: [] for region in target_regions}
        
    def is_target_reached():
        total_collected = sum(len(ips) for ips in valid_ips_by_region.values())
        if is_scan_all and total_collected >= all_mode_limit_count:
            return True
        elif not is_scan_all and all(len(ips) >= sync_count for ips in valid_ips_by_region.values()):
            return True
        return False
        
    def process_ips(ips_to_test):
        success_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = {executor.submit(test_ip, ip, check_api_url): ip for ip in ips_to_test}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    colo = result.get('colo', 'UNK').upper()
                    if colo != 'UNK' and (is_scan_all or colo in target_regions):
                        success_count += 1
                        if colo not in valid_ips_by_region:
                            valid_ips_by_region[colo] = []
                            
                        # 检查是否重复
                        if any(ip_obj['ip'] == result['ip'] for ip_obj in valid_ips_by_region[colo]):
                            continue
                            
                        if is_scan_all:
                            total_collected = sum(len(ips) for ips in valid_ips_by_region.values())
                            if total_collected < all_mode_limit_count:
                                valid_ips_by_region[colo].append(result)
                                print(f"[FOUND {colo}] {result['ip']} (Total ALL: {total_collected + 1}/{all_mode_limit_count})")
                        else:
                            if len(valid_ips_by_region[colo]) < sync_count:
                                valid_ips_by_region[colo].append(result)
                                print(f"[FOUND {colo}] {result['ip']} (Total {colo}: {len(valid_ips_by_region[colo])}/{sync_count})")
                                
                if is_target_reached():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
        return success_count

    # Phase 1: Test historical IPs first (Skip in Global Scan mode)
    if historical_ips and not is_scan_all:
        print(f"\nPhase 1: Testing {len(historical_ips)} historical IPs from ips-v4.txt...")
        process_ips(historical_ips)
    elif is_scan_all:
        print("\n[Global Mode] Skipping Phase 1 (Historical IPs)...")

    # Phase 2: Generate from hot subnets if target not reached (Skip in Global Scan mode)
    if not is_target_reached() and hot_cidrs and not is_scan_all:
        print(f"\nPhase 2: Target not reached. Scanning individual hot subnets...")
        for cidr in hot_cidrs:
            if is_target_reached():
                break
                
            print(f"--- Scanning Hot Subnet: {cidr} ---")
            # 测试该段的 100 个随机 IP (约 40% 的覆盖率)
            ips_to_test = [generate_random_ip_from_cidrs([cidr]) for _ in range(100)]
            success_count = process_ips(ips_to_test)
            
            if success_count == 0:
                print(f"[DEAD SUBNET] No valid IPs found in {cidr}. (Keeping in log per user request)")
            else:
                print(f"[ACTIVE SUBNET] {cidr} is alive ({success_count} responsive IPs).")
            
    # Phase 3: Generate from all subnets in ip.txt if target still not reached
    if not is_target_reached():
        print(f"\nPhase 3: Target not reached. Scanning IPs from global subnets (ip.txt)...")
        attempt = 0
        max_attempts = 10
        while attempt < max_attempts and not is_target_reached():
            attempt += 1
            print(f"--- Global Subnets Scan Iteration {attempt} ---")
            ips_to_test = [generate_random_ip_from_cidrs(CF_CIDRS) for _ in range(500)]
            process_ips(ips_to_test)
                    
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
        limit = all_mode_limit_count if is_scan_all else sync_count
        best_ips = ips[:limit]
        all_best_ips.extend(best_ips)
        
        print(f"\n--- Top {len(best_ips)} IPs Selected for {region} ---")
        for ip in best_ips:
            print(f"IP: {ip['ip']:<15} | Latency: {ip['latency']:>3}ms | Colo: {ip['colo']}")
            
        # Target domain specific to this region
        if can_sync:
            if is_scan_all:
                print(f"\n[Global Mode] Skipping regional subdomain sync for {region}.")
            else:
                target_domain = f"{region.lower()}.{base_domain}"
                print(f"\nStarting Cloudflare DNS Sync for {target_domain}...")
                sync_to_cloudflare(api_token, zone_id, target_domain, best_ips, cf_email)
        else:
            print(f"\nSkipping Cloudflare DNS Sync for {region} (Missing Credentials).")
                
    if can_sync and all_best_ips:
        # 在 ALL 模式下，强制同步到主域名；在精准模式下，取决于 SYNC_MAIN_DOMAIN 开关
        if is_scan_all or SYNC_MAIN_DOMAIN.strip().upper() == "YES":
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

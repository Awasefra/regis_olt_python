import csv, sys, time, os, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import paramiko

SEND_DELAY_SEC = 0.1
READ_WINDOW_SEC = 0.4
LOG_CSV = "hasil_registrasi.csv"

progress_lock = threading.Lock()
progress_dict = {}

def load_done_keys(log_path):
    done = set()
    if not os.path.exists(log_path):
        return done
    with open(log_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if (row.get("status") or "").lower() == "success":
                key = (row.get("interface","").strip(),
                       row.get("onu_id","").strip(),
                       row.get("sn","").strip())
                done.add(key)
    return done

def append_log(lock, path, row_dict):
    with lock:
        rows = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        existing_keys = {(r["interface"], r["onu_id"], r["sn"]) for r in rows}

        key = (row_dict["interface"], row_dict["onu_id"], row_dict["sn"])
        if key in existing_keys:
            return

        file_exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            fieldnames = ["interface","onu_id","sn","name","status","message"]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                w.writeheader()
            w.writerow(row_dict)
            f.flush()

def ssh_connect(cfg):
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(cfg["host"], port=cfg["port"], username=cfg["user"], password=cfg["pass"], look_for_keys=False)
    sh = cli.invoke_shell()
    time.sleep(0.5)
    if sh.recv_ready():
        _ = sh.recv(65535)
    return cli, sh

def send_block(shell, block, delay=SEND_DELAY_SEC, read_window=READ_WINDOW_SEC):
    if not block.endswith("\n"):
        block += "\n"
    shell.send(block)
    time.sleep(delay)
    end = time.time() + read_window
    out = []
    while time.time() < end:
        time.sleep(0.05)
        while shell.recv_ready():
            out.append(shell.recv(65535).decode(errors="ignore"))
            end = time.time() + 0.3
    return "".join(out)

def enter_config(shell):
    send_block(shell, "enable")
    send_block(shell, "configure terminal")

def build_onu_block(row, vlan_prefix):
    interface   = row["interface"].strip()
    onu_id      = row["onu_id"].strip()
    sn          = row["sn"].strip()
    name        = row["name"].strip().replace(" ", "_")
    desc        = row["description"].strip()
    profile     = row["profile"].strip()
    username    = row["username"].strip()
    password    = row["password"].strip()
    vlan_inet   = row["vlan_inet"].strip()
    vlan_hot    = row["vlan_hotspot"].strip()
    ssid        = row["wifi_ssid"].strip()

    onu_iface   = f"gpon-onu_{interface.split('_')[1]}:{onu_id}"
    vlan_prof   = f"{vlan_prefix}{vlan_inet}"

    block = f"""interface {interface}
onu {onu_id} type ALL sn {sn}
exit
interface {onu_iface}
name {name}
description {desc}
tcont 1 profile {profile}
gemport 1 tcont 1
service-port 1 vport 1 user-vlan {vlan_inet} vlan {vlan_inet}
service-port 2 vport 1 user-vlan {vlan_hot} vlan {vlan_hot}
exit
pon-onu-mng {onu_iface}
service pppoe gemport 1 vlan {vlan_inet}
wan-ip 1 mode pppoe username {username} password {password} vlan-profile {vlan_prof} host 1
service hotspot gemport 1 vlan {vlan_hot}
vlan port wifi_0/4 mode tag vlan {vlan_hot}
ssid auth wep wifi_0/4 open-system
ssid ctrl wifi_0/4 name {ssid}
interface wifi wifi_0/4 state unlock
exit
"""
    return block

def process_port(interface, rows, log_lock, cfg):
    cli, sh = ssh_connect(cfg)
    try:
        enter_config(sh)
        with progress_lock:
            progress_dict[interface] = {"done":0,"total":len(rows),"status":"RUNNING"}

        add_lines = [f"interface {interface}"]
        for r in rows:
            onu_id = r["onu_id"].strip()
            sn = r["sn"].strip()
            add_lines.append(f"onu {onu_id} type ALL sn {sn}")
        add_lines.append("exit")
        send_block(sh, "\n".join(add_lines))

        for r in rows:
            try:
                block = build_onu_block(r, cfg["vlan_prefix"])
                out = send_block(sh, block)
                status = "success" if "%Error" not in out and "Invalid" not in out else "error"
                append_log(log_lock, LOG_CSV, {
                    "interface": r["interface"], "onu_id": r["onu_id"], "sn": r["sn"],
                    "name": r["name"], "status": status, "message": "-"
                })
            except Exception as e:
                append_log(log_lock, LOG_CSV, {
                    "interface": r["interface"], "onu_id": r["onu_id"], "sn": r["sn"],
                    "name": r["name"], "status": "error", "message": str(e)[:180]
                })
            with progress_lock:
                progress_dict[interface]["done"] += 1
    finally:
        cli.close()
        with progress_lock:
            progress_dict[interface]["status"] = "FINISHED"

def main(csv_path, cfg):
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    done = load_done_keys(LOG_CSV)
    pending = [r for r in reader if (r["interface"].strip(), r["onu_id"].strip(), r["sn"].strip()) not in done]

    if not pending:
        print("✅ Semua baris sudah success di log. Tidak ada pekerjaan.")
        return "no_pending"

    ports = {}
    for r in pending:
        ports.setdefault(r["interface"].strip(), []).append(r)
    for p in ports:
        progress_dict[p] = {"done":0,"total":len(ports[p]),"status":"WAITING"}

    log_lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=cfg.get("max_workers", 6)) as ex:
        futures = []
        for iface, rows in ports.items():
            futures.append(ex.submit(process_port, iface, rows, log_lock, cfg))
            with progress_lock:
                progress_dict[iface]["status"] = "RUNNING"
        for fut in as_completed(futures):
            fut.result()
    print("✅ Semua port selesai. Cek hasil_registrasi.csv")
    return "done"

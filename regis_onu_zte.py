import csv, sys, time, os, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import paramiko
import os
from dotenv import load_dotenv
load_dotenv()


# =====================[ KONFIGURASI ]=====================
OLT_HOST   = os.getenv("OLT_HOST")
OLT_PORT   = int(os.getenv("OLT_PORT"))
OLT_USER   = os.getenv("OLT_USER")
OLT_PASS   = os.getenv("OLT_PASS")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 6))
VLAN_PROFILE_PREFIX = os.getenv("VLAN_PROFILE_PREFIX")

# Model/tipe ONU. Untuk C300 sering aman pakai "ALL" atau "ZTE-F601"
ONU_TYPE   = os.getenv("OLT_PASS", "ALL")

# Tuning delay baca CLI
SEND_DELAY_SEC = 0.1
READ_WINDOW_SEC = 0.4

LOG_CSV    = "hasil_registrasi.csv"
# =========================================================

progress_lock = threading.Lock()
progress_dict = {}  # { "1/2/1": {"done":3,"total":40,"status":"RUNNING"} }

# =========================================================
def load_done_keys(log_path):
    """Baca log sukses agar bisa resume."""
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
    """Append atau replace baris log secara thread-safe."""
    with lock:
        rows = []
        replaced = False

        # Baca semua baris lama (kalau file ada)
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                for row in reader:
                    # Kalau baris cocok → replace
                    if (row["interface"] == row_dict["interface"] and
                        row["onu_id"] == row_dict["onu_id"] and
                        row["sn"] == row_dict["sn"]):
                        rows.append(row_dict)
                        replaced = True
                    else:
                        rows.append(row)

        # Kalau belum ada → tambahkan baru
        if not replaced:
            rows.append(row_dict)

        # Tulis ulang seluruh CSV
        with open(path, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["interface","onu_id","sn","name","status","message"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)



def ssh_connect():
    """Buat koneksi SSH baru & open shell."""
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(OLT_HOST, port=OLT_PORT, username=OLT_USER, password=OLT_PASS, look_for_keys=False)
    sh = cli.invoke_shell()
    time.sleep(0.5)
    if sh.recv_ready():
        _ = sh.recv(65535)
    return cli, sh


def send_block(shell, block, delay=SEND_DELAY_SEC, read_window=READ_WINDOW_SEC):
    """Kirim block CLI lalu baca output singkat."""
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


def build_onu_block(row):
    """Rangkai perintah untuk 1 ONU."""
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
    vlan_prof   = f"{VLAN_PROFILE_PREFIX}{vlan_inet}"

    block = f"""\
interface {interface}
onu {onu_id} type {ONU_TYPE} sn {sn}
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


def process_port(interface, rows, log_lock):
    cli, sh = ssh_connect()
    try:
        enter_config(sh)
        with progress_lock:
            progress_dict[interface] = {"done": 0, "total": len(rows), "status": "RUNNING"}

        # Tambahkan semua ONU dalam satu blok
        add_lines = [f"interface {interface}"]
        for r in rows:
            onu_id = r["onu_id"].strip()
            sn = r["sn"].strip()
            add_lines.append(f"onu {onu_id} type {ONU_TYPE} sn {sn}")
        add_lines.append("exit")
        send_block(sh, "\n".join(add_lines))

        # Konfigurasi masing-masing ONU
        for r in rows:
            try:
                block = build_onu_block(r)
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


def main(csv_path="uploads/data_onu.csv"):
    """Fungsi utama yang bisa dipanggil dari Flask."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    done = load_done_keys(LOG_CSV)
    pending = [r for r in reader if (r["interface"].strip(), r["onu_id"].strip(), r["sn"].strip()) not in done]

    if not pending:
        print("Semua baris sudah success di log. Tidak ada pekerjaan.")
        return "no_pending"

    ports = {}
    for r in pending:
        ports.setdefault(r["interface"].strip(), []).append(r)
    total_onu = len(pending)

    # inisialisasi status WAITING
    for p in ports:
        progress_dict[p] = {"done": 0, "total": len(ports[p]), "status": "WAITING"}

    log_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = []
        for iface, rows in ports.items():
            futures.append(ex.submit(process_port, iface, rows, log_lock))
            with progress_lock:
                progress_dict[iface]["status"] = "RUNNING"
        for fut in as_completed(futures):
            fut.result()

    # try:
    #     cli, sh = ssh_connect()
    #     send_block(sh, "enable")
    #     send_block(sh, "write", delay=1.5, read_window=3)
    #     cli.close()
    # except Exception as e:
    #     print(f"⚠️ Gagal write: {e}")

    print("✅ Semua port selesai. Cek hasil_registrasi.csv")
    return "done"


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDibatalkan oleh user.")
        sys.exit(1)

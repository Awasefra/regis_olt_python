from flask import Flask, request, render_template_string, jsonify
import threading, os, csv
from regis_onu_zte import progress_dict, main as run_regis_main

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
LOG_CSV = "hasil_registrasi.csv"

# 🔹 status global proses (register/config/done)
current_phase = {"phase": "idle"}


@app.route("/")
def index():
    return render_template_string("""
    <html>
    <head>
      <title>OLT ZTE Auto Registration</title>
      <style>
        :root {
          --bg: #121212;
          --card: #1e1e1e;
          --border: #2a2a2a;
          --text: #e0e0e0;
          --accent: #00bcd4;
          --success: #4CAF50;
          --wait: #FFC107;
          --done: #673AB7;
        }
        body {
          background: var(--bg);
          color: var(--text);
          font-family: "Segoe UI", Arial, sans-serif;
          padding: 30px;
          max-width: 1000px;
          margin: auto;
        }
        h2, h3 { color: #fff; }
        form {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 15px 25px;
          background: var(--card);
          padding: 20px;
          border-radius: 12px;
          border: 1px solid var(--border);
        }
        form label {
          font-size: 13px;
          color: #aaa;
          display: block;
          margin-bottom: 4px;
        }
        input, button {
          padding: 9px;
          font-size: 14px;
          border-radius: 6px;
          border: 1px solid var(--border);
          background: #181818;
          color: var(--text);
          width: 100%;
          box-sizing: border-box;
        }
        button {
          cursor: pointer;
          background: linear-gradient(90deg, var(--accent), #2196f3);
          color: #fff;
          border: none;
          font-weight: bold;
          transition: 0.2s;
        }
        button:hover { opacity: 0.9; }
        .full-width { grid-column: 1 / 3; }
        .bar {
          height: 18px;
          background: var(--border);
          border-radius: 6px;
          overflow: hidden;
          margin-bottom: 8px;
        }
        .fill { height: 100%; transition: width 0.5s; }
        .legend { margin-top:10px; font-size:14px; }
        .legend span {
          display:inline-block; width:15px; height:15px;
          margin-right:5px; border-radius:3px;
        }
        table {
          border-collapse: collapse;
          width: 100%;
          margin-top:15px;
          background: var(--card);
          border-radius: 8px;
          overflow: hidden;
        }
        th, td {
          border: 1px solid var(--border);
          padding: 8px;
          text-align: left;
          font-size: 13px;
        }
        th { background: #2b2b2b; color: #fff; }
        tr:nth-child(even) { background: #181818; }
        #status { margin-top: 15px; font-weight: bold; }

        #results-area{
          margin-top:10px;
          max-height:500px;
          overflow-y:auto;
          border:1px solid var(--border);
          border-radius:8px;
        }
        #results-area table{ width:100%; border-collapse:collapse; }
        #results-area th, #results-area td{
          border:1px solid var(--border);
          padding:6px;
          font-size:13px;
        }
        #results-area th{
          position:sticky; top:0;
          background:#2b2b2b; color:#fff;
        }
        footer {
          text-align: center;
          color: #777;
          font-size: 13px;
          margin-top: 40px;
        }
        footer a {
          color: var(--accent);
          text-decoration: none;
        }
        footer a:hover { text-decoration: underline; }
      </style>
    </head>
    <body>
      <h2>📡 OLT ZTE Auto Registration</h2>

      <form id="uploadForm">
        <div>
          <label>OLT Host</label>
          <input type="text" name="olt_host" placeholder="10.240.x.x" required>
        </div>
        <div>
          <label>OLT Port</label>
          <input type="number" name="olt_port" value="22" required>
        </div>
        <div>
          <label>OLT User</label>
          <input type="text" name="olt_user" value="zte" required>
        </div>
        <div>
          <label>OLT Password</label>
          <input type="password" name="olt_pass" required>
        </div>
        <div>
          <label>VLAN Prefix</label>
          <input type="text" name="vlan_prefix" placeholder="vlan" required>
        </div>
        <div>
        <label>Worker Mode</label>
          <input type="text" value="Auto (1 for Register, 2 for Config)" readonly>
        </div>
        <div  class="full-width">
          <label>File CSV ONU (1 port saja)</label>
          <input type="file" name="file" accept=".csv" required>
        </div>
        <div  class="full-width">
          <label>
            <input type="checkbox" name="auto_write" value="true" style="width:auto;vertical-align:middle;margin-right:6px;">
            Jalankan <b>write</b> otomatis setelah registrasi (commit konfigurasi)
          </label>
        </div>
        <div class="full-width">
          <button type="submit">🚀 Upload & Jalankan</button>
        </div>
      </form>

      <div id="status"></div>
      <hr>
      <div id="progress-area"></div>
      <hr>
      <div class="legend">
        <span style="background:var(--wait)"></span> Pending
        <span style="background:var(--success)"></span> Registered/Success
        <span style="background:#f44336"></span> Error
      </div>

      <hr>
      <h3>📘 Hasil Registrasi</h3>
      <div id="results-area"></div>

      <footer>
        <p>© 2025 <a href="https://dasaria.id" target="_blank">Dasaria Development Team</a> — All rights reserved.</p>
        <p>Made with 💻 by Awanda Setya</p>
      </footer>

      <script>
      document.getElementById("uploadForm").addEventListener("submit", async function(e) {
        e.preventDefault();
        const form = e.target;
        const formData = new FormData(form);
        document.getElementById("status").innerHTML = "⏳ Mengupload dan memulai proses...";
        try {
          const res = await fetch("/upload", { method: "POST", body: formData });
          const data = await res.json();
          if (data.error) {
            document.getElementById("status").innerHTML = data.error;
          } else {
            document.getElementById("status").innerHTML = data.success;
          }
        } catch (err) {
          document.getElementById("status").innerHTML = "❌ Gagal upload: " + err;
        }
      });

      async function refreshProgress() {
        const res = await fetch('/progress');
        const data = await res.json();
        let html = "";
        for (const [port, v] of Object.entries(data)) {
          const pct = (v.total ? (v.done / v.total * 100) : 0).toFixed(0);
          html += `<div><b>${port}</b> - ${v.status} (${v.done}/${v.total})</div>
                   <div class='bar'><div class='fill' style='width:${pct}%; background:var(--accent);'></div></div>`;
        }
        document.getElementById("progress-area").innerHTML = html;
        setTimeout(refreshProgress, 2000);
      }

      async function loadResults() {
        const scroller = document.getElementById("results-area");
        const prevTop = scroller.scrollTop;
        const prevHeight = scroller.scrollHeight;
        const atBottom = prevTop + scroller.clientHeight >= prevHeight - 5;

        const res = await fetch("/results", { cache: "no-store" });
        const html = await res.text();
        scroller.innerHTML = html;

        const newHeight = scroller.scrollHeight;
        if (atBottom) scroller.scrollTop = scroller.scrollHeight;
        else scroller.scrollTop = prevTop + (newHeight - prevHeight);

        setTimeout(loadResults, 5000);
      }

      refreshProgress();
      loadResults();
      </script>
    </body>
    </html>
    """)


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "❌ Tidak ada file diupload."}), 400

    path = os.path.join(UPLOAD_FOLDER, "data_onu.csv")
    file.save(path)

    # ✅ Validasi hanya 1 port di CSV
    with open(path, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    ports = {r["interface"].strip() for r in reader if r.get("interface")}
    if len(ports) != 1:
        return jsonify({
            "error": f"❌ File harus berisi 1 port saja, ditemukan {len(ports)} port: {', '.join(ports)}"
        }), 400
        
    auto_write = request.form.get("auto_write") == "true"
    
    olt_config = {
        "host": request.form.get("olt_host"),
        "port": int(request.form.get("olt_port")),
        "user": request.form.get("olt_user"),
        "pass": request.form.get("olt_pass"),
        "vlan_prefix": request.form.get("vlan_prefix"),
        "max_workers": 1,
        "auto_write": auto_write,
    }

    def threaded_run():
        try:
            current_phase["phase"] = "register"
            run_regis_main(path, olt_config, mode="register")
            current_phase["phase"] = "config"
            run_regis_main(path, olt_config, mode="config")
            current_phase["phase"] = "done"
        except Exception as e:
            current_phase["phase"] = f"error: {e}"

    t = threading.Thread(target=threaded_run, daemon=True)
    t.start()
    return jsonify({
        "success": f"✅ File diterima. Menjalankan registrasi untuk {list(ports)[0]} di OLT {olt_config['host']}."
    })


@app.route("/progress")
def progress():
    return jsonify(progress_dict)


@app.route("/results")
def results():
    if not os.path.exists(LOG_CSV):
        return "<i>Belum ada hasil registrasi.</i>"
    with open(LOG_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return "<i>Belum ada hasil registrasi.</i>"

    html = "<table><tr>" + "".join([f"<th>{h}</th>" for h in rows[0].keys()]) + "</tr>"
    for r in rows:
        status = r.get("status", "").lower()
        color = (
            "var(--wait)" if status == "pending"
            else "var(--success)" if status in ["registered", "success"]
            else "#f44336" if status == "error"
            else "var(--text)"
        )
        html += "<tr>" + "".join([
            f"<td style='color:{color};font-weight:bold'>{v}</td>" if k == "status" else f"<td>{v}</td>"
            for k, v in r.items()
        ]) + "</tr>"
    html += "</table>"

    return html


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, use_reloader=False)

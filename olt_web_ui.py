from flask import Flask, request, render_template_string, jsonify
import threading, os, csv
from dotenv import load_dotenv
from regis_onu_zte import main as run_registration, progress_dict

load_dotenv()
app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
LOG_CSV = "hasil_registrasi.csv"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/")
def index():
    return render_template_string("""
    <html>
    <head>
      <title>OLT ZTE Auto Registration</title>
      <style>
        body { font-family: Arial; background: #fafafa; padding: 30px; }
        .bar { height: 20px; background: #eee; border-radius: 8px; overflow: hidden; margin-bottom: 8px; }
        .fill { height: 100%; background: linear-gradient(90deg,#4CAF50,#8BC34A); transition: width 0.5s; }
        table { border-collapse: collapse; width: 100%; margin-top: 10px; }
        th, td { border: 1px solid #ddd; padding: 8px; font-size: 14px; }
        th { background: #f2f2f2; }
        h2 { color: #333; }
        input,button { padding:8px; margin:5px 0; }
        #status { margin-top: 10px; font-weight: bold; }
      </style>
    </head>
    <body>
      <h2>📡 OLT ZTE Auto Registration</h2>
      <form id="uploadForm">
        <input type="file" id="csvFile" name="file" accept=".csv" required>
        <button type="submit">Upload & Jalankan</button>
      </form>
      <div id="status"></div>
      <hr>
      <div id="progress-area"></div>

      <hr>
      <h3>📋 Hasil Registrasi</h3>
      <div id="result-table">Belum ada hasil.</div>

      <script>
      // ===== Upload TANPA reload =====
      document.getElementById("uploadForm").addEventListener("submit", async function(e) {
        e.preventDefault();
        const fileInput = document.getElementById("csvFile");
        if (!fileInput.files.length) {
          document.getElementById("status").innerHTML = "❌ Silakan pilih file CSV dulu.";
          return;
        }
        const formData = new FormData();
        formData.append("file", fileInput.files[0]);
        document.getElementById("status").innerHTML = "⏳ Mengupload dan memulai proses...";
        try {
          const res = await fetch("/upload", { method: "POST", body: formData });
          const text = await res.text();
          document.getElementById("status").innerHTML = text;
        } catch (err) {
          document.getElementById("status").innerHTML = "❌ Gagal upload: " + err;
        }
      });

      // ===== Auto-refresh progress bar =====
      async function refreshProgress() {
        const res = await fetch('/progress');
        const data = await res.json();
        let html = "";
        let totalDone = 0;
        let totalTotal = 0;
        for (const [port, v] of Object.entries(data)) {
          const pct = (v.total ? (v.done / v.total * 100) : 0).toFixed(0);
          totalDone += v.done;
          totalTotal += v.total;
          html += `<div><b>${port}</b> - ${v.status} (${v.done}/${v.total})</div>
                   <div class='bar'><div class='fill' style='width:${pct}%;'></div></div>`;
        }
        if (totalTotal > 0) {
          const pctAll = (totalDone / totalTotal * 100).toFixed(0);
          html = `<h3>Total Progress: ${totalDone}/${totalTotal} (${pctAll}%)</h3>
                  <div class='bar'><div class='fill' style='width:${pctAll}%;'></div></div><br>` + html;
        }
        document.getElementById("progress-area").innerHTML = html;
        setTimeout(refreshProgress, 2000);
      }
      refreshProgress();

      // ===== Auto-refresh hasil registrasi =====
      async function refreshResults() {
        const res = await fetch('/results');
        const html = await res.text();
        document.getElementById("result-table").innerHTML = html;
        setTimeout(refreshResults, 5000);
      }
      refreshResults();
      </script>
    </body>
    </html>
    """)

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file:
        return "❌ Tidak ada file diupload", 400
    path = os.path.join(UPLOAD_FOLDER, "data_onu.csv")
    file.save(path)

    def background_run():
        result = run_registration(path)
        if result == "no_pending":
            print("⚠️ Tidak ada pekerjaan baru, semua sudah success.")

    t = threading.Thread(target=background_run, daemon=True)
    t.start()

    # Cek dulu, kalau langsung tidak ada pekerjaan, tampilkan ke UI
    result = run_registration(path)
    if result == "no_pending":
        return "⚠️ Semua baris sudah success di log. Tidak ada pekerjaan baru."

    return "✅ File diterima, proses registrasi sedang berjalan!"


@app.route("/progress")
def progress():
    return jsonify(progress_dict)

@app.route("/results")
def results():
    """Tampilkan hasil_registrasi.csv dalam bentuk tabel HTML."""
    if not os.path.exists(LOG_CSV):
        return "<i>Belum ada hasil registrasi.</i>"

    with open(LOG_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return "<i>Belum ada data hasil.</i>"

    headers = reader.fieldnames
    html = "<table><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    for row in rows[-50:]:  # tampilkan 50 data terakhir
        html += "<tr>" + "".join(f"<td>{row[h]}</td>" for h in headers) + "</tr>"
    html += "</table>"
    return html

if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", 8000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)


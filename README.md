# Archi MCP v1.0 (macOS)

MCP server agar Claude bisa membaca & mengubah model ArchiMate di **Archi** (macOS, Apple Silicon).
Satu file Python (`archi_mcp.py`), dependensi diurus otomatis oleh `uv`.

## Prasyarat (Pre-requisite)

Yang dibutuhkan, semuanya di Mac:

| Kebutuhan | Keterangan | Cara pasang |
|---|---|---|
| macOS | Versi 1.0 hanya untuk Mac (diuji di Apple Silicon) | - |
| Homebrew | Pengelola paket Mac | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| Archi | Aplikasi ArchiMate. Harus ada di `/Applications/Archi.app` (diuji di Archi 5.10.0) | `brew install --cask archi`, atau unduh dari https://www.archimatetool.com/download/ lalu seret ke folder Applications |
| uv | Menjalankan script Python dan memasang dependensinya otomatis (Python 3.10+ ikut diurus uv, tidak perlu pasang manual) | `brew install uv` |
| Claude | Claude Code atau Claude Desktop sebagai MCP client | https://claude.com/download |
| Git | Untuk clone repo ini (sudah ada di Mac dengan Xcode Command Line Tools) | `xcode-select --install` |

### Langkah pasang

1. Pasang Homebrew, Archi, dan uv seperti tabel di atas.
2. Buka Archi sekali supaya folder datanya terbentuk, lalu tutup.
3. Clone repo:
   ```bash
   git clone https://github.com/buildwithderry/mcp_archi.git
   cd mcp_archi
   ```
4. Cek server jalan (dependensi terpasang otomatis saat pertama kali):
   ```bash
   uv run --script smoke_test.py
   ```
   Hasil yang benar: `22 tools` dan daftar model Archi kamu.
5. Daftarkan ke Claude (lihat bagian **Pasang** di bawah), lalu restart Claude.

Jika Archi tidak di `/Applications/Archi.app`, set env `ARCHI_APP`.

## Cara kerja
- Membaca/menulis file `.archimate` langsung (format native Archi). Setiap penulisan membuat cadangan `*.archimate.bak`.
- Validasi relasi memakai aturan ArchiMate dari `relationships.xml` milik Archi yang terpasang.
- Ekspor (HTML report, CSV, Open Exchange XML, gambar PNG view) memakai command line Archi (`/Applications/Archi.app`).

> **Penting:** Archi tidak memuat ulang file yang diubah dari luar. Tutup model di Archi sebelum Claude mengubahnya,
> lalu buka lagi (atau minta Claude memanggil `open_in_archi`).

## Tools
| Kategori | Tools |
|---|---|
| Baca | `list_models`, `get_model_summary`, `list_elements`, `list_relationships`, `get_concept`, `list_views`, `get_view`, `list_types`, `list_valid_relationships` |
| Ubah | `create_model`, `add_element`, `add_elements`, `update_concept`, `add_relationship`, `delete_concept`, `create_view`, `add_to_view` |
| Archi | `open_in_archi`, `export_html_report`, `export_csv`, `export_open_exchange`, `export_view_images` |

## Pasang

Claude Code (termasuk tab Code di aplikasi Claude):

```bash
claude mcp add archi --scope user -- uv run --script /Users/derryderajat/MCP/mcp_archi/archi_mcp.py
```

Claude Desktop (tab Chat) — tambahkan ke `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "archi": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--script", "/Users/derryderajat/MCP/mcp_archi/archi_mcp.py"]
    }
  }
}
```

Jika Archi tidak di `/Applications/Archi.app`, set env `ARCHI_APP`.

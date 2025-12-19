# Qualtrics Report Generator

**A Python utility that converts Qualtrics CSV exports into beautifully formatted, human-readable HTML reports.**

Built for **administrative data intake surveys** where different respondents complete different sections—such as institutional reporting surveys, compliance questionnaires, and rankings data collection—where you need to review each response in context rather than aggregate statistics.

---

## 🎯 Why This Tool Exists

Standard Qualtrics reports are designed for traditional surveys with many respondents answering the same questions. But many institutional surveys work differently:

| Traditional Survey | Administrative Data Intake |
|-------------------|---------------------------|
| 500 people answer Q1-Q20 | 7 administrators each answer their assigned section |
| You want: percentages, charts, trends | You want: readable responses organized by question |
| Qualtrics Reports work great | Qualtrics Reports are awkward and hard to review |

**This tool solves the data intake problem** by generating a clean HTML report where each question displays its respondent(s) and their answers in a readable format—perfect for review, validation, and sharing.

---

## 📋 Use Cases

### 1. Institutional Rankings Surveys
> *"We submit data to U.S. News for medical school rankings. Seven different administrators each complete their section (Admissions, Financial Aid, Research, etc.). I need to review all responses before submission."*

### 2. Accreditation & Compliance Reporting
> *"Our accreditation self-study survey has 200+ questions across 15 departments. Each department head answers their section. I need to compile and review everything."*

### 3. Annual Data Collection
> *"Every year we collect enrollment, financial, and outcomes data from program directors. I need a readable report to verify numbers before they go to leadership."*

### 4. Grant Progress Reports
> *"Multiple PIs report on their grant activities quarterly. I need to review all responses and share a summary with the grants office."*

### 5. Multi-Stakeholder Intake Forms
> *"Different offices submit budget requests through Qualtrics. Finance needs to review all submissions in one document."*

### 6. Quality Assurance Review
> *"Before submitting our IPEDS data, I need to review every response to catch errors, missing data, or inconsistencies."*

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **QSF Metadata Support** | Uses your survey definition file for accurate question text and choice labels |
| **Smart Question Detection** | Auto-recognizes single, grouped, matrix, and form question types |
| **Matrix Table Rendering** | Displays matrix responses as proper HTML tables with row/column headers |
| **Dual Interface** | GUI for interactive use, CLI for automation and scripting |
| **Rich Value Formatting** | Detects URLs, files, coordinates, JSON, dates, and more |
| **Colorblind-Friendly** | Accessible color palette (blue/teal/orange) safe for all vision types |
| **XSS-Safe Output** | All content escaped—safe to host or share publicly |
| **Debug Mode** | Optional metadata showing question types and column counts |

---

## 📦 Requirements

**Python 3.8+** with pandas:
```bash
pip install pandas
```

### Linux – Installing Tkinter (for GUI)
```bash
# Debian/Ubuntu
sudo apt-get install python3-tk

# Fedora/RHEL
sudo dnf install python3-tkinter
```

> **Note:** The CLI works without Tkinter. GUI is optional.

---

## 🚀 Installation
```bash
# Download the script
curl -O https://raw.githubusercontent.com/your-username/qualtrics-report-generator/main/qualtrics_report_generator.py

# Or clone the repository
git clone https://github.com/your-username/qualtrics-report-generator.git
cd qualtrics-report-generator
```

---

## 📥 Exporting Data from Qualtrics

### Step 1: Export CSV with Choice Text

1. Open your survey in Qualtrics
2. Go to **Data & Analysis** → **Export & Import** → **Export Data**
3. Select **CSV** format
4. **Important:** Under "More Options", ensure:
   - ☑️ **Use choice text** (not numeric values)
   - ☑️ **Include display order columns** (optional but helpful)
5. Click **Download**

![Export Settings](docs/export-settings.png)

### Step 2: Download QSF File (Recommended)

The QSF file contains your survey definition with proper question text and choice labels. **This produces much better reports.**

1. Go to **Survey** tab
2. Click **Tools** → **Import/Export** → **Export Survey**
3. Save the `.qsf` file in the same folder as your CSV

### File Naming Tip

For automatic QSF detection, name your files consistently:
```
my_survey.csv
my_survey.qsf
```

The tool will automatically find the QSF if it shares the same base name.

---

## 🖥️ Usage

### GUI Mode (Default)
```bash
python qualtrics_report_generator.py
```

1. **Browse** → Select your CSV file
2. **Browse** → Select your QSF file (optional but recommended)
3. **Browse** → Choose output location
4. Optionally check **Include debug info**
5. Click **Generate Report**

### CLI Mode
```bash
# Basic usage (CSV only)
python qualtrics_report_generator.py survey.csv

# With QSF for accurate labels (recommended)
python qualtrics_report_generator.py -q survey.qsf survey.csv

# Specify output file
python qualtrics_report_generator.py -q survey.qsf -o report.html survey.csv

# With debug info
python qualtrics_report_generator.py -q survey.qsf -d survey.csv

# Write processing log
python qualtrics_report_generator.py -q survey.qsf -l debug.log survey.csv
```

### CLI Options

| Flag | Long Form | Description |
|------|-----------|-------------|
| `-q` | `--qsf` | Path to QSF file for accurate question metadata |
| `-o` | `--output` | Output HTML file path (default: `qualtrics_report.html`) |
| `-d` | `--debug` | Include debug metadata in HTML output |
| `-l` | `--log` | Write detailed processing log to file |
| `-h` | `--help` | Show help message |

---

## 📊 Supported Question Types

| Question Type | Column Pattern | How It's Displayed |
|--------------|----------------|-------------------|
| **Single Text Entry** | `Q1` | Plain text |
| **Multiple Choice** | `Q1` | Selected option(s) |
| **Matrix (Text Entry)** | `Q1_1_2` | HTML table with row/column headers |
| **Matrix (Likert)** | `Q1_1_2` | HTML table with selections |
| **Form Fields** | `Q1_1`, `Q1_2` | Label: value pairs |
| **Grouped Items** | `Q1_1`, `Q1_2` | Structured list or table |
| **Multi-Select** | Comma-separated | Bullet list |
| **File Upload** | URL detected | 📎 Download link |
| **Slider/Scale** | Numeric | Formatted number |
| **Drill-Down** | `>` or `→` separators | Breadcrumb display |

---

## 📄 Output Format

The generator produces a **single, self-contained HTML file** with embedded CSS. No external dependencies—just open in any browser.

### Report Structure
```
┌─────────────────────────────────────────────────────────────┐
│  📋 Qualtrics Survey Report                                 │
│  Generated on December 19, 2025 at 2:30 PM                  │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────────────┐              │
│  │    7    │  │   105   │  │       42        │              │
│  │Respond. │  │Questions│  │ Matrix Questions│              │
│  └─────────┘  └─────────┘  └─────────────────┘              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Q47                                                  │   │
│  │─────────────────────────────────────────────────────│   │
│  │ Total Medical School Enrollment by Gender            │   │
│  │─────────────────────────────────────────────────────│   │
│  │ Joan St. Onge                                        │   │
│  │ Response: R_7GH3gPp5oZlNW5n                          │   │
│  │                                                      │   │
│  │ ┌──────────────────┬───────┬───────┬───────┬──────┐ │   │
│  │ │                  │ Men   │ Women │ Other │ Total│ │   │
│  │ ├──────────────────┼───────┼───────┼───────┼──────┤ │   │
│  │ │ First Year Class │ 102   │ 98    │ 2     │ 202  │ │   │
│  │ │ Second Year Class│ 98    │ 104   │ 1     │ 203  │ │   │
│  │ │ Third Year Class │ 95    │ 107   │ 2     │ 204  │ │   │
│  │ │ Fourth Year Class│ 99    │ 101   │ 1     │ 201  │ │   │
│  │ │ Total Enrollment │ 394   │ 410   │ 6     │ 810  │ │   │
│  │ └──────────────────┴───────┴───────┴───────┴──────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Q66                                                  │   │
│  │─────────────────────────────────────────────────────│   │
│  │ Tuition and Fees                                     │   │
│  │─────────────────────────────────────────────────────│   │
│  │ Maria Rodriguez                                      │   │
│  │ Response: R_2xKmNp8wQzYh3Jv                          │   │
│  │                                                      │   │
│  │ ┌──────────────────────┬────────────┬────────────┐  │   │
│  │ │                      │ 2025-2026  │ 2024-2025  │  │   │
│  │ ├──────────────────────┼────────────┼────────────┤  │   │
│  │ │ In-State Tuition     │ $43,500    │ $42,000    │  │   │
│  │ │ Out-of-State Tuition │ $67,800    │ $65,500    │  │   │
│  │ │ Required Fees        │ $1,250     │ $1,200     │  │   │
│  │ │ Room & Board         │ $18,500    │ $17,800    │  │   │
│  │ └──────────────────────┴────────────┴────────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎨 Color Palette

Uses a colorblind-friendly palette based on Paul Tol's research:

| Element | Color | Hex | Usage |
|---------|-------|-----|-------|
| Primary | Blue | `#0077BB` | Headers, links, accents |
| Primary Dark | Navy | `#004488` | Table headers, gradients |
| Success | Teal | `#009988` | Single response indicator |
| Warning | Orange | `#EE7733` | Code indicators |
| Neutral | Gray | `#718096` | Metadata, timestamps |

Safe for deuteranopia, protanopia, and tritanopia.

---

## 🔧 With vs Without QSF

| Aspect | Without QSF | With QSF |
|--------|-------------|----------|
| Question text | Extracted from CSV headers (may be truncated) | Full question text from survey definition |
| Row labels | Generic ("Row 1", "Row 2") or inferred from CSV | Actual choice labels ("In-State Tuition", "Men") |
| Column labels | Generic ("Column 1") or inferred | Actual answer labels ("2024-2025", "2025-2026") |
| Question types | Inferred from column patterns | Definitive from survey metadata |

**Recommendation:** Always include the QSF file for best results.

---

## 🛠️ Technical Details

| Aspect | Detail |
|--------|--------|
| **Python Version** | 3.8+ |
| **Dependencies** | pandas (required), tkinter (optional for GUI) |
| **HTML Output** | Self-contained with embedded CSS |
| **Security** | All content escaped via `html.escape()` |
| **Encoding** | UTF-8-sig (handles BOM from Excel) |
| **Performance** | Uses `to_dict('records')` for efficient row processing |

---

## ❗ Troubleshooting

| Issue | Solution |
|-------|----------|
| **"Row 1", "Row 2" labels** | Include the QSF file with `-q` flag |
| **Missing responses** | Check that CSV was exported with "Use choice text" |
| **Garbled characters** | Re-export CSV from Qualtrics (encoding issue) |
| **GUI won't launch** | Install tkinter: `apt install python3-tk` |
| **Empty report** | Verify CSV has data rows (not just headers) |

---

## 📁 Project Structure
```
qualtrics-report-generator/
├── qualtrics_report_generator.py   # Main script (GUI + CLI)
├── README.md                       # This file
├── LICENSE                         # CC BY-NC-ND 4.0
└── examples/
    ├── sample_survey.csv           # Example export
    ├── sample_survey.qsf           # Example survey definition
    └── sample_report.html          # Example output
```

---

## 📜 License

**Qualtrics Report Generator** © 2025 Philip Bachas-Daunert

Distributed under the [Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International](https://creativecommons.org/licenses/by-nc-nd/4.0/) license.

---

## 🙏 Acknowledgments

- **pandas** – Data manipulation engine
- **Tkinter** – Cross-platform GUI framework
- **Paul Tol** – Colorblind-safe palette research
- **Anthropic Claude** – Development assistance

---

## 💡 Tips for Best Results

1. **Always export with "Use choice text"** – Numeric codes require the QSF to decode
2. **Download both CSV and QSF** – QSF provides accurate labels
3. **Use consistent naming** – `survey.csv` + `survey.qsf` enables auto-detection
4. **Review in debug mode first** – Helps identify any parsing issues
5. **Re-export if needed** – Qualtrics exports can sometimes be inconsistent
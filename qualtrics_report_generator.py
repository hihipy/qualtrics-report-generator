"""
Qualtrics Report Generator
==========================

A tool to convert Qualtrics CSV exports into readable HTML reports.
Optionally uses QSF (Survey Definition) files for accurate question metadata.

Features:
    - Automatic question type detection from QSF files
    - Proper labels for choices and answers from survey definition
    - Falls back to CSV pattern inference if no QSF provided
    - Handles all Qualtrics question types: Matrix, MC, TE, Form, etc.
    - Colorblind-friendly HTML output with responsive design
    - CLI and GUI interfaces

Usage:
    CLI: python qualtrics_report_generator.py -q survey.qsf survey.csv
    GUI: python qualtrics_report_generator.py (no arguments)
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from html import escape, unescape

import pandas as pd

# Optional GUI support - gracefully degrade if tkinter unavailable
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


# =============================================================================
# CONSTANTS
# =============================================================================

# Detection thresholds for content type classification
LONG_TEXT_THRESHOLD = 200          # Characters before text is considered "long"
NUMERIC_CODE_MAX = 20              # Max value for single-digit Qualtrics codes
NUMERIC_CODE_RANGE_MIN = 100       # Min value for multi-digit codes
NUMERIC_CODE_RANGE_MAX = 300       # Max value for multi-digit codes
MULTI_VALUE_AVG_LENGTH_MAX = 40    # Max avg length for multi-value detection
SHORT_VALUE_MAX_LENGTH = 30        # Max length for categorical values
MAX_CATEGORICAL_UNIQUE_RATIO = 0.5 # Threshold for unique vs categorical data

# Timing column patterns (Qualtrics automatically adds these metadata columns)
TIMING_SUFFIXES = (
    '_Page Submit', '_First Click', '_Last Click', '_Click Count',
    '_PageSubmit', '_FirstClick', '_LastClick', '_ClickCount'
)

# Metadata columns to skip (Qualtrics system fields that aren't survey responses)
METADATA_COLUMNS = {
    'StartDate', 'EndDate', 'Status', 'IPAddress', 'Progress',
    'Duration (in seconds)', 'Finished', 'RecordedDate', 'ResponseId',
    'RecipientLastName', 'RecipientFirstName', 'RecipientEmail',
    'ExternalReference', 'LocationLatitude', 'LocationLongitude',
    'DistributionChannel', 'UserLanguage', 'Browser', 'Version',
    'Operating System', 'Resolution', 'DeviceType', 'Q_TotalDuration',
    'Q_URL', 'Q_BallotBoxStuffing', 'Q_RelevantIDDuplicate'
}

# Regex patterns for cleaning boilerplate text from question text
# These are common instructional phrases that clutter the display
BOILERPLATE_PATTERNS = [
    r'\s*See Email Titled\s*"[^"]*"[^.]*\.?\s*',
    r'\s*See Email Titled[^.]*\.?\s*',
    r'\s*This data is rolled over from last year\.?\s*',
    r'\s*This question is used in the Rankings calculation\.?\s*',
    r'\s*RESPONSE NEEDED\s*',
    r'\s*ACTION NEEDED\s*',
    r'\s*DUE \d{1,2}/\d{1,2}[^.]*\.?\s*',
    r'\s*for PDF of Results from Last Year\.?\s*',
    r'\s*-\s*U\.S\. News Best Medical Schools Survey\s*',
    r'\s*U\.S\. News Best Medical Schools Survey\s*',
    r'\s*Click to write the question text\s*',
    r'\s*Please answer the following\.?\s*',
]

# Keywords indicating legitimate numeric responses (not Qualtrics codes)
# Used to distinguish real numeric data from internal selection codes
NUMERIC_QUESTION_KEYWORDS = [
    'number', 'count', 'total', 'how many', 'percent', '%',
    'year', 'age', 'score', 'gpa', 'mcat', 'credits', 'hours',
    'fee', 'tuition', 'salary', 'amount', '$', 'usd', 'dollar',
    'phone', 'zip', 'code', 'id', 'size', 'rate', 'ratio',
    'enrollment', 'residents', 'men', 'women', 'graduates',
    'indebtedness', 'funds', 'faculty', 'research', 'nih',
    'rank', 'rating', 'scale', 'slider', 'nps', 'score'
]

# File extensions for detecting file upload responses
FILE_EXTENSIONS = (
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.jpg', '.jpeg',
    '.png', '.gif', '.mp3', '.mp4', '.zip', '.csv', '.txt'
)

# Colorblind-friendly palette for HTML output (Wong palette + neutrals)
COLORS = {
    'primary': '#0077BB',       # Blue - main accent
    'primary_dark': '#004488',  # Dark blue - headers
    'success': '#009988',       # Teal - positive indicators
    'warning': '#EE7733',       # Orange - warnings
    'warning_dark': '#CC6600',  # Dark orange - code indicators
    'error': '#CC3311',         # Red - errors
    'neutral': '#718096',       # Gray - secondary text
    'light': '#f8fafc',         # Light gray - backgrounds
    'border': '#e2e8f0',        # Border color
}

# Question type mapping from QSF (QuestionType, Selector, SubSelector) to internal types
# This maps Qualtrics' internal question definitions to our display logic
QSF_TYPE_MAP = {
    # Matrix questions - rows × columns grid format
    ('Matrix', 'TE', 'Long'): 'matrix_text',
    ('Matrix', 'TE', 'Short'): 'matrix_text',
    ('Matrix', 'TE', None): 'matrix_text',
    ('Matrix', 'Likert', 'SingleAnswer'): 'matrix_likert',
    ('Matrix', 'Likert', 'MultipleAnswer'): 'matrix_multi',
    ('Matrix', 'Likert', None): 'matrix_likert',
    ('Matrix', 'Profile', None): 'matrix_likert',
    ('Matrix', 'Bipolar', None): 'matrix_likert',
    ('Matrix', 'MaxDiff', None): 'matrix_likert',

    # Text entry questions - free-form text responses
    ('TE', 'FORM', None): 'form',          # Multi-field form
    ('TE', 'SL', None): 'single_text',     # Single line
    ('TE', 'ML', None): 'multi_text',      # Multi-line
    ('TE', 'ESTB', None): 'essay',         # Essay box
    ('TE', None, None): 'single_text',

    # Multiple choice questions - single or multi-select
    ('MC', 'SAVR', 'TX'): 'single_choice',   # Single answer vertical
    ('MC', 'SAVR', None): 'single_choice',
    ('MC', 'SAHR', None): 'single_choice',   # Single answer horizontal
    ('MC', 'SACOL', None): 'single_choice',  # Single answer column
    ('MC', 'MAVR', 'TX'): 'multi_choice',    # Multiple answer vertical
    ('MC', 'MAVR', None): 'multi_choice',
    ('MC', 'MAHR', None): 'multi_choice',    # Multiple answer horizontal
    ('MC', 'MACOL', None): 'multi_choice',   # Multiple answer column
    ('MC', 'DL', None): 'single_choice',     # Dropdown list
    ('MC', 'RB', None): 'single_choice',     # Radio button
    ('MC', 'NPS', None): 'single_choice',    # Net Promoter Score

    # Display blocks - informational, not real questions
    ('DB', 'TB', None): 'display',    # Text block
    ('DB', 'GRB', None): 'display',   # Graphic block

    # Slider questions - continuous scale input
    ('Slider', 'HBAR', None): 'single_text',
    ('Slider', 'HSLIDER', None): 'single_text',

    # Side by Side - complex multi-column format
    ('SBS', None, None): 'matrix_text',
}


# =============================================================================
# LOGGING SETUP
# =============================================================================

logger = logging.getLogger('QualtricsReportGenerator')


def setup_logging(debug=False, log_file=None):
    """
    Configure logging with optional file output.

    Sets up console logging and optionally file logging for debugging.
    Debug mode provides detailed information about parsing and processing.

    Args:
        debug: If True, sets logging level to DEBUG; otherwise WARNING.
        log_file: Optional path to write log output to the file.
    """
    level = logging.DEBUG if debug else logging.WARNING
    logger.setLevel(level)
    logger.handlers.clear()

    # Console handler with a simple format
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # Optional file handler with a detailed format including function names
    if log_file:
        file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        logger.addHandler(file_handler)


# =============================================================================
# QSF PARSING
# =============================================================================

def parse_qsf(qsf_path):
    """
    Parse a Qualtrics QSF file to extract question metadata.

    The QSF (Qualtrics Survey Format) file contains the complete survey
     definition, including question types, choice labels, answer labels,
    and display logic. This function extracts the metadata needed to
    properly label and format responses in the HTML report.

    Args:
        qsf_path: Path to the .qsf file.

    Returns:
        Dictionary mapping DataExportTag (Q1, Q2, etc.) to question metadata
        including: qid, text, qsf_type, selector, subselector, internal_type,
        choices, choice_order, answers, answer_order, recode_values.
    """
    logger.info(f"Parsing QSF file: {qsf_path}")

    with open(qsf_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    questions = {}

    # Iterate through all Survey Question (SQ) elements
    for element in data.get('SurveyElements', []):
        if element.get('Element') != 'SQ':
            continue

        payload = element.get('Payload', {})
        qid = element.get('PrimaryAttribute', '')
        export_tag = payload.get('DataExportTag', '')

        # Skip questions without export tags (internal/hidden questions)
        if not export_tag:
            continue

        # Extract question type information for format mapping
        qtype = payload.get('QuestionType', '')
        selector = payload.get('Selector', '')
        subselector = payload.get('SubSelector')

        # Map the QSF type to an internal display type
        type_key = (qtype, selector, subselector)
        internal_type = QSF_TYPE_MAP.get(type_key)

        # Fallback: try without subselector
        if not internal_type:
            type_key_no_sub = (qtype, selector, None)
            internal_type = QSF_TYPE_MAP.get(type_key_no_sub, 'unknown')

        # Extract and clean question text (may contain HTML)
        question_text = payload.get('QuestionText', '')
        question_text = clean_html_text(question_text)

        # --- Extract choices (row labels for matrix, options for MC) ---
        choices = {}
        choice_order = payload.get('ChoiceOrder', [])
        raw_choices = payload.get('Choices', {})

        # Process choices in order if ChoiceOrder is provided
        if choice_order:
            for choice_id in choice_order:
                choice_id_str = str(choice_id)
                if choice_id_str in raw_choices:
                    choice_data = raw_choices[choice_id_str]
                    if isinstance(choice_data, dict):
                        # Try 'Display' first, then 'Text' as fallback
                        display = choice_data.get(
                            'Display',
                            choice_data.get('Text', f'Choice {choice_id}')
                        )
                        choices[choice_id_str] = clean_html_text(display)
                    else:
                        choices[choice_id_str] = clean_html_text(str(choice_data))

        # Fallback: iterate raw_choices if no order provided
        if not choices and raw_choices:
            for cid, cdata in raw_choices.items():
                if isinstance(cdata, dict):
                    display = cdata.get(
                        'Display',
                        cdata.get('Text', f'Choice {cid}')
                    )
                    choices[cid] = clean_html_text(display)
                else:
                    choices[cid] = clean_html_text(str(cdata))

        # --- Extract answers (column headers for matrix questions) ---
        answers = {}
        answer_order = payload.get('AnswerOrder', [])
        raw_answers = payload.get('Answers', {})

        # Process answers in order if AnswerOrder is provided
        if answer_order:
            for answer_id in answer_order:
                answer_id_str = str(answer_id)
                if answer_id_str in raw_answers:
                    answer_data = raw_answers[answer_id_str]
                    if isinstance(answer_data, dict):
                        display = answer_data.get(
                            'Display',
                            answer_data.get('Text', f'Answer {answer_id}')
                        )
                        answers[answer_id_str] = clean_html_text(display)
                    else:
                        answers[answer_id_str] = clean_html_text(str(answer_data))

        # Fallback: iterate raw_answers if no order provided
        if not answers and raw_answers:
            for aid, adata in raw_answers.items():
                if isinstance(adata, dict):
                    display = adata.get(
                        'Display',
                        adata.get('Text', f'Answer {aid}')
                    )
                    answers[aid] = clean_html_text(display)
                else:
                    answers[aid] = clean_html_text(str(adata))

        # Check for ColumnLabels (used in some matrix types)
        column_labels = payload.get('ColumnLabels', {})
        if column_labels and not answers:
            for col_id, col_data in column_labels.items():
                if isinstance(col_data, dict):
                    answers[col_id] = clean_html_text(
                        col_data.get('Display', f'Column {col_id}')
                    )
                else:
                    answers[col_id] = clean_html_text(str(col_data))

        # Store recode values for potential future use
        recode_values = payload.get('RecodeValues', {})

        # Build question metadata dictionary
        questions[export_tag] = {
            'qid': qid,
            'export_tag': export_tag,
            'text': question_text,
            'qsf_type': qtype,
            'selector': selector,
            'subselector': subselector,
            'internal_type': internal_type,
            'choices': choices,
            'choice_order': (
                [str(c) for c in choice_order]
                if choice_order
                else list(choices.keys())
            ),
            'answers': answers,
            'answer_order': (
                [str(a) for a in answer_order]
                if answer_order
                else list(answers.keys())
            ),
            'recode_values': recode_values,
        }

    logger.info(f"Parsed {len(questions)} questions from QSF")
    return questions


def clean_html_text(text):
    """
    Remove HTML tags and clean up question text.

    Qualtrics stores question text with HTML formatting. This function
    removes tags, decodes entities, and strips boilerplate instructions
    to produce clean display text.

    Args:
        text: HTML-formatted text from QSF.

    Returns:
        Plain text with HTML removed and whitespace normalized.
    """
    if not text:
        return ''

    # Decode HTML entities first (QSF may contain &amp;, &nbsp;, etc.)
    text = unescape(text)

    # Remove real HTML tags (now that entities are decoded)
    text = re.sub(r'<[^>]+>', ' ', text)

    # Apply boilerplate patterns to remove common instructional text
    for pattern in BOILERPLATE_PATTERNS:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)

    # Normalize whitespace and return
    return re.sub(r'\s+', ' ', text).strip()


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def safe_str(value):
    """
    Safely convert any value to a stripped string.

    Handles NaN/None values gracefully by returning an empty string.

    Args:
        value: Any value (maybe None, NaN, or any type).

    Returns:
        String representation, stripped of whitespace.
    """
    if pd.isna(value):
        return ''
    return str(value).strip()


def safe_html(value):
    """
    Escape value for safe HTML output.

    Prevents XSS and rendering issues by escaping special HTML characters.

    Args:
        value: Any value to be displayed in HTML.

    Returns:
        HTML-escaped string safe for embedding in HTML.
    """
    return escape(safe_str(value))


def is_empty(value):
    """
    Check if a value is empty or null.

    Args:
        value: Value to check.

    Returns:
        True if the value is NaN, None, or empty string.
    """
    return pd.isna(value) or safe_str(value) == ''


def contains_any(text, keywords):
    """
    Check if the text contains any of the keywords (case-insensitive).

    Args:
        text: Text to search.
        keywords: List of keywords to look for.

    Returns:
        True if any keyword is found in the text.
    """
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def sort_row_key(key):
    """
    Generate a sort key for row ordering.

    Sorts numeric keys first (by value), then alphabetic keys.
    This ensures rows display in intuitive order (1, 2, 3, ... then A, B, C).

    Args:
        key: Row key (typically string like '1', '2', 'a', 'b').

    Returns:
        Tuple for sorting: (type_priority, numeric_value, alpha_value).
    """
    key_str = str(key)
    if key_str.isdigit():
        return (0, int(key_str), '')
    return (1, 0, key_str.lower())


def is_numeric_value(value):
    """
    Check if a value is numeric (integer, float, or formatted number).

    Handles common numeric formats including commas, dollar signs, and
    percentage symbols.

    Args:
        value: Value to check.

    Returns:
        True if value can be parsed as a number.
    """
    val = safe_str(value)
    if not val:
        return False

    # Remove common formatting characters
    cleaned = val.replace(',', '').replace('$', '').replace('%', '').strip()

    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def values_are_numeric_data(values):
    """
    Determine if values represent numeric data points vs. categorical selections.

    Used to decide between data table display (for numbers) and checkmark
    table display (for categories).

    Args:
        values: List of values to analyze.

    Returns:
        True if values appear to be numeric data entries.
    """
    if not values:
        return False

    non_empty = [v for v in values if safe_str(v)]
    if not non_empty:
        return False

    numeric_count = sum(1 for v in non_empty if is_numeric_value(v))
    numeric_ratio = numeric_count / len(non_empty)

    # High ratio of numeric values indicates data
    if numeric_ratio >= 0.7:
        return True

    # Many unique values with moderate numeric ratio also indicates data
    unique_values = set(safe_str(v) for v in non_empty)
    if len(unique_values) > 5 and numeric_ratio >= 0.5:
        return True

    return False


def values_are_unique_data(values):
    """
    Determine if values are unique data entries vs repeated categorical selections.

    Unique entries (names, emails, dates) should be displayed as data tables,
    while repeated selections should use checkmark tables.

    Args:
        values: List of values to analyze.

    Returns:
        True if values appear to be unique data entries.
    """
    if not values:
        return False

    non_empty = [safe_str(v) for v in values if safe_str(v)]
    if not non_empty:
        return False

    unique_values = set(non_empty)
    unique_ratio = len(unique_values) / len(non_empty)

    # High uniqueness ratio suggests data entries
    if unique_ratio > MAX_CATEGORICAL_UNIQUE_RATIO:
        return True

    # Variable lengths with many unique values suggests data
    if len(unique_values) > 5:
        lengths = set(len(v) for v in unique_values)
        if len(lengths) > 3:
            return True

    # Check for common data patterns (emails, dates, multi-word entries)
    data_pattern_count = 0
    for val in non_empty:
        # Email pattern
        if '@' in val and '.' in val:
            data_pattern_count += 1
        # Date pattern
        elif re.match(r'^\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}$', val):
            data_pattern_count += 1
        # Multi-word text (names, descriptions)
        elif len(val.split()) >= 2 and len(val) > 10:
            data_pattern_count += 1

    if data_pattern_count > 0 and data_pattern_count >= len(non_empty) * 0.3:
        return True

    return False


# =============================================================================
# VALUE TYPE DETECTION
# =============================================================================

def is_numeric_code(value, question_text=''):
    """
    Check if the value appears to be a Qualtrics numeric code rather than real data.

    Qualtrics exports may contain internal selection codes (1, 2, 3) instead
    of label text. This function distinguishes codes from legitimate numeric
    responses using question context.

    Args:
        value: Value to check.
        question_text: Question text for context (numeric keywords).

    Returns:
        True if the value appears to be an internal code.
    """
    val = safe_str(value)
    if not val:
        return False

    # If the question asks for numbers, treat numeric values as data
    if contains_any(question_text, NUMERIC_QUESTION_KEYWORDS):
        return False

    # Comma-separated small numbers are likely multi-select codes
    if ',' in val:
        parts = val.split(',')
        if all(p.strip().isdigit() and len(p.strip()) <= 3 for p in parts):
            return True

    # Single small integers are likely codes
    if val.isdigit():
        num = int(val)
        if 1 <= num <= NUMERIC_CODE_MAX:
            return True
        # Certain range indicates codes (100-300, non-round numbers)
        if (NUMERIC_CODE_RANGE_MIN <= num <= NUMERIC_CODE_RANGE_MAX
                and num % 100 != 0):
            return True

    return False


def is_url(value):
    """Check if the value is a URL."""
    val = safe_str(value).lower()
    return any(val.startswith(p) for p in ('http://', 'https://', 'www.', 'ftp://'))


def is_file_path(value):
    """Check if the value looks like a filename."""
    val = safe_str(value).lower()
    return any(val.endswith(ext) for ext in FILE_EXTENSIONS)


def is_coordinate(value):
    """Check if the value looks like coordinate/heat map data."""
    val = safe_str(value)
    patterns = [
        r'^\d+\.?\d*\s*,\s*\d+\.?\d*$',           # 123,456
        r'^\(\d+\.?\d*\s*,\s*\d+\.?\d*\)$',       # (123,456)
        r'^\d+\.?\d*\s*:\s*\d+\.?\d*$',           # 123:456
        r'^x:\s*\d+\.?\d*\s*,?\s*y:\s*\d+\.?\d*$',  # x:123,y:456
    ]
    return any(re.match(p, val, re.IGNORECASE) for p in patterns)


def is_timing_column(column_id):
    """Check if column contains timing metadata."""
    return any(suffix in column_id for suffix in TIMING_SUFFIXES)


def is_json(value):
    """Check if the value looks like JSON data."""
    val = safe_str(value)
    return ((val.startswith('{') and val.endswith('}'))
            or (val.startswith('[') and val.endswith(']')))


def is_hierarchical(value):
    """Check if the value is hierarchical/drill-down data."""
    val = safe_str(value)
    return ' > ' in val or ' >> ' in val or ' → ' in val


def is_multi_value(value, separator=','):
    """
    Check if the value is a multi-value list.

    Distinguishes genuine multi-value responses from coordinates or
    numeric codes.

    Args:
        value: Value to check.
        separator: Separator character to look for.

    Returns:
        True if value appears to be a multi-value list.
    """
    val = safe_str(value)
    if separator not in val:
        return False

    parts = val.split(separator)

    # Small numbers separated by commas are likely codes, not lists
    if all(p.strip().isdigit() and len(p.strip()) <= 3 for p in parts):
        return False

    # Coordinates aren't multi-value lists
    if is_coordinate(value):
        return False

    # Need at least 2 significant parts
    text_parts = [p.strip() for p in parts if len(p.strip()) > 2]
    if len(text_parts) < 2:
        return False

    # Very long average part length suggests prose, not a list
    avg_length = sum(len(p) for p in text_parts) / len(text_parts)
    if avg_length > MULTI_VALUE_AVG_LENGTH_MAX:
        return False

    return True


def is_long_text(value):
    """Check if the value is long-form text."""
    return len(safe_str(value)) > LONG_TEXT_THRESHOLD


def detect_value_type(value, question_text='', column_id=''):
    """
    Detect the type of value for appropriate formatting.

    Analyzes the value content to determine the best display format.

    Args:
        value: The value to classify.
        question_text: Question text for context.
        column_id: Column ID for timing detection.

    Returns:
        String type identifier: 'empty', 'code', 'url', 'file', 'coordinate',
        'timing', 'json', 'hierarchical', 'pipe_list', 'semicolon_list',
        'comma_list', 'long_text', or 'text'.
    """
    if is_empty(value):
        return 'empty'

    if is_timing_column(column_id):
        return 'timing'

    if is_url(value):
        return 'file' if is_file_path(value) else 'url'

    if is_file_path(value):
        return 'file'

    if is_coordinate(value):
        return 'coordinate'

    if is_json(value):
        return 'json'

    if is_hierarchical(value):
        return 'hierarchical'

    # Check multi-value with different separators
    if is_multi_value(value, '|'):
        return 'pipe_list'

    if is_multi_value(value, ';'):
        return 'semicolon_list'

    if is_multi_value(value, ','):
        return 'comma_list'

    if is_long_text(value):
        return 'long_text'

    if is_numeric_code(value, question_text):
        return 'code'

    return 'text'


# =============================================================================
# VALUE FORMATTERS
# =============================================================================

def format_empty():
    """Format empty/no response indicator."""
    return "<span class='no-response'>No response</span>"


def format_text(value):
    """Format plain text with newline handling."""
    return safe_html(value).replace('\n', '<br>')


def format_code(value):
    """
    Format numeric code indicator.

    Shows that the value is an internal Qualtrics code rather than
    display text.
    """
    val = safe_str(value)
    if ',' in val:
        codes = [c.strip() for c in val.split(',')]
        return f"<span class='code-value'>[Selections: {', '.join(codes)}]</span>"
    return f"<span class='code-value'>[Code: {val}]</span>"


def format_url(value):
    """Format clickable URL."""
    val = safe_str(value)
    href = val if val.startswith(('http://', 'https://')) else f'https://{val}'
    return (
        f"<a href='{safe_html(href)}' target='_blank' class='url-link'>"
        f"{safe_html(val)}</a>"
    )


def format_file(value):
    """Format file upload/attachment reference."""
    val = safe_str(value)
    if is_url(val):
        filename = val.split('/')[-1]
        return (
            f"<span class='file-upload'>📎 "
            f"<a href='{safe_html(val)}' target='_blank'>"
            f"{safe_html(filename)}</a></span>"
        )
    return f"<span class='file-upload'>📎 {safe_html(val)}</span>"


def format_coordinate(value):
    """Format coordinate/heat map data."""
    val = safe_str(value)
    match = re.search(r'(\d+\.?\d*)\s*[,:]\s*(\d+\.?\d*)', val)
    if match:
        x, y = match.groups()
        return f"<span class='coordinate'>📍 X: {x}, Y: {y}</span>"
    return f"<span class='coordinate'>📍 {safe_html(val)}</span>"


def format_timing(value, column_id):
    """
    Format timing metadata with human-readable labels.

    Converts raw timing values to friendly format (e.g., "2m 30s").
    """
    val = safe_str(value)

    # Determine a label based on a column type
    if 'Page Submit' in column_id or 'PageSubmit' in column_id:
        label = "Page time"
    elif 'First Click' in column_id or 'FirstClick' in column_id:
        label = "First click"
    elif 'Last Click' in column_id or 'LastClick' in column_id:
        label = "Last click"
    elif 'Click Count' in column_id or 'ClickCount' in column_id:
        return f"<span class='timing'>🖱️ Clicks: {safe_html(val)}</span>"
    else:
        label = "Time"

    # Convert seconds to human-readable format
    try:
        seconds = float(val)
        if seconds >= 60:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"<span class='timing'>⏱️ {label}: {minutes}m {secs}s</span>"
        return f"<span class='timing'>⏱️ {label}: {seconds:.1f}s</span>"
    except ValueError:
        return f"<span class='timing'>⏱️ {label}: {safe_html(val)}</span>"


def format_json(value):
    """Format JSON data with pretty printing."""
    try:
        data = json.loads(safe_str(value))
        formatted = json.dumps(data, indent=2)
        return f"<pre class='json-data'>{safe_html(formatted)}</pre>"
    except (json.JSONDecodeError, ValueError):
        return f"<pre class='json-data'>{safe_html(value)}</pre>"


def format_hierarchical(value):
    """Format hierarchical/drill-down data with visual breadcrumb trail."""
    val = safe_str(value)

    # Detect a separator type
    for sep in [' >> ', ' → ', ' > ']:
        if sep in val:
            parts = val.split(sep)
            break
    else:
        parts = [val]

    # Build breadcrumb HTML
    html_parts = ["<div class='drill-down'>"]
    for i, part in enumerate(parts):
        if i > 0:
            html_parts.append("<span class='drill-arrow'>›</span>")
        html_parts.append(f"<span class='drill-level'>{safe_html(part.strip())}</span>")
    html_parts.append("</div>")

    return ''.join(html_parts)


def format_list(value, separator):
    """Format multi-value list as bullet points."""
    parts = [p.strip() for p in safe_str(value).split(separator) if p.strip()]
    if not parts:
        return format_empty()

    html_parts = ["<ul class='vertical-list'>"]
    for part in parts:
        html_parts.append(f"<li>{safe_html(part)}</li>")
    html_parts.append("</ul>")

    return ''.join(html_parts)


def format_long_text(value):
    """Format long-form text with paragraph handling."""
    val = safe_str(value)
    paragraphs = [p.strip() for p in val.split('\n') if p.strip()]

    if len(paragraphs) > 1:
        html_parts = ["<div class='long-text'>"]
        for p in paragraphs:
            html_parts.append(f"<p>{safe_html(p)}</p>")
        html_parts.append("</div>")
        return ''.join(html_parts)

    return f"<div class='long-text'>{safe_html(val)}</div>"


def format_value(value, question_text='', column_id=''):
    """
    Main entry point for formatting a value.

    Detects the value type and applies appropriate formatting.

    Args:
        value: The value to format.
        question_text: Question text for context.
        column_id: Column ID for type detection.

    Returns:
        HTML-formatted string for display.
    """
    value_type = detect_value_type(value, question_text, column_id)

    formatters = {
        'empty': format_empty,
        'code': lambda: format_code(value),
        'url': lambda: format_url(value),
        'file': lambda: format_file(value),
        'coordinate': lambda: format_coordinate(value),
        'timing': lambda: format_timing(value, column_id),
        'json': lambda: format_json(value),
        'hierarchical': lambda: format_hierarchical(value),
        'pipe_list': lambda: format_list(value, '|'),
        'semicolon_list': lambda: format_list(value, ';'),
        'comma_list': lambda: format_list(value, ','),
        'long_text': lambda: format_long_text(value),
        'text': lambda: format_text(value),
    }

    return formatters.get(value_type, lambda: format_text(value))()


# =============================================================================
# QUESTION TEXT PROCESSING
# =============================================================================

def clean_question_text(text):
    """
    Remove boilerplate and formatting artifacts from question text.

    Used for CSV-based parsing when QSF metadata isn't available.

    Args:
        text: Raw question text from CSV header.

    Returns:
        Cleaned question text.
    """
    if pd.isna(text):
        return ''

    text = str(text)

    # Remove boilerplate patterns
    for pattern in BOILERPLATE_PATTERNS:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)

    # Clean up dashes and formatting
    text = re.sub(r'^\s*-\s*', '', text)
    text = re.sub(r'\s*-\s*$', '', text)
    text = re.sub(r'\s*-\s*-\s*', ' ', text)
    text = re.sub(r'\n{2,}', ' ', text)
    text = re.sub(r'\t+', ' ', text)

    return ' '.join(text.split()).strip()


def extract_matrix_labels(text):
    """
    Extract row and column labels from matrix question text.

    Qualtrics CSV headers encode matrix cell info as:
    "Question Text - Row Label - Column Label"

    Args:
        text: Column header text from CSV.

    Returns:
        Tuple of (base_question_text, row_label, column_label).
        Labels may be None if not found.
    """
    if pd.isna(text):
        return ('', None, None)

    cleaned = clean_question_text(str(text))

    # Protect date ranges like "2024 - 2025" from being split
    protected = re.sub(r'(\d{4})\s*-\s*(\d{4})', r'\1__DATERANGE__\2', cleaned)

    # Split on "-" separator
    parts = re.split(r'\s+-\s+', protected)

    # Restore date ranges
    parts = [p.replace('__DATERANGE__', ' - ') for p in parts]

    # Parse based on number of parts
    if len(parts) >= 3:
        return (' - '.join(parts[:-2]), parts[-2], parts[-1])
    elif len(parts) == 2:
        return (parts[0], parts[1], None)

    return (cleaned, None, None)


# =============================================================================
# CSV PARSING
# =============================================================================

def validate_csv(csv_path):
    """
    Validate that file exists and is a readable CSV.

    Args:
        csv_path: Path to CSV file.

    Raises:
        FileNotFoundError: If a file doesn't exist.
        ValueError: If a file cannot be read as CSV.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"File not found: {csv_path}")

    if not csv_path.lower().endswith('.csv'):
        logger.warning("File does not have .csv extension")

    try:
        pd.read_csv(csv_path, nrows=1, encoding='utf-8-sig')
    except Exception as e:
        raise ValueError(f"Cannot read CSV file: {e}")


def read_csv_safe(csv_path, **kwargs):
    """
    Read CSV with resilient settings to prevent crashes.

    Uses string dtype to prevent type coercion issues and warns on
    bad lines instead of failing.

    Args:
        csv_path: Path to CSV file.
        **kwargs: Additional arguments to pass to pd.read_csv.

    Returns:
        DataFrame with all columns as strings.
    """
    return pd.read_csv(
        csv_path,
        encoding='utf-8-sig',
        dtype=str,
        on_bad_lines='warn',
        **kwargs
    )


def get_label_from_qsf(qsf_info, csv_index, label_type='choice'):
    """
    Map CSV sequential index to QSF label.

    CSV uses sequential 1-based indices (1, 2, 3) for rows,
    but QSF uses actual choice IDs (35, 36, 37). This function
    handles the mapping between the two systems.

    Args:
        qsf_info: Dictionary with 'choices', 'choice_order', 'answers', 'answer_order'.
        csv_index: The index from the CSV column (e.g., '1', '2', '5').
        Label_type: 'choice' for row labels, 'answer' for column labels.

    Returns:
        The label string or a fallback like "Row X".
    """
    if label_type == 'choice':
        choices = qsf_info.get('choices', {})
        choice_order = qsf_info.get('choice_order', [])

        # First, try direct lookup (in case the CSV index matches QSF ID)
        if csv_index in choices:
            return choices[csv_index]

        # Otherwise, treat CSV index as 1-based position in choice_order
        try:
            idx = int(csv_index) - 1  # Convert to 0-based
            if 0 <= idx < len(choice_order):
                qsf_id = choice_order[idx]
                if qsf_id in choices:
                    return choices[qsf_id]
        except (ValueError, IndexError):
            pass

        return f"Row {csv_index}"

    else:  # answer
        answers = qsf_info.get('answers', {})
        answer_order = qsf_info.get('answer_order', [])

        # First, try direct lookup (CSV often uses actual answer IDs for columns)
        if csv_index in answers:
            return answers[csv_index]

        # Otherwise, treat as a 1-based position
        try:
            idx = int(csv_index) - 1
            if 0 <= idx < len(answer_order):
                qsf_id = answer_order[idx]
                if qsf_id in answers:
                    return answers[qsf_id]
        except (ValueError, IndexError):
            pass

        return f"Column {csv_index}"


def extract_questions_with_qsf(csv_path, qsf_metadata):
    """
    Extract the question structure using QSF metadata for accurate types.

    Combines CSV column structure with QSF metadata to produce accurate
    question definitions with proper labels.

    Args:
        csv_path: Path to CSV file for column structure.
        qsf_metadata: Dictionary from parse_qsf() with question definitions.

    Returns:
        Dictionary of questions with definitive types from QSF.
    """
    logger.info("Extracting questions with QSF metadata...")

    # Read header rows to get column structure
    df_header = read_csv_safe(csv_path, nrows=2)
    columns = df_header.columns.tolist()
    texts = df_header.iloc[0].tolist()

    questions = {}

    for col_id, col_text in zip(columns, texts):
        # Skip metadata and timing columns
        if col_id in METADATA_COLUMNS:
            continue
        if is_timing_column(col_id):
            continue
        if not col_id.startswith('Q'):
            continue

        # Parse column ID patterns: Q1, Q1_1, Q1_1_2, Q1_TEXT, Q1_1_TEXT
        match = re.match(r'Q(\d+)(?:_(\d+))?(?:_(\d+))?(_TEXT)?$', col_id)
        if not match:
            # Try an alternate pattern: Q1_other
            match = re.match(r'Q(\d+)(?:_(.+))?$', col_id)
            if not match:
                continue

        base_q = f"Q{match.group(1)}"

        # Extract subindices based on match groups
        if len(match.groups()) >= 4:
            sub1 = match.group(2)
            sub2 = match.group(3)
            is_text_field = match.group(4) is not None
        elif len(match.groups()) >= 2:
            sub1 = match.group(2)
            sub2 = None
            is_text_field = False
        else:
            sub1 = None
            sub2 = None
            is_text_field = False

        # Handle non-numeric subparts (like Q1_TEXT)
        if sub1 and not sub1.isdigit():
            if sub1 == 'TEXT':
                is_text_field = True
                sub1 = None

        # Skip standalone _TEXT fields for grouped questions
        if is_text_field and sub1:
            continue

        # Look up QSF metadata for this question
        qsf_info = qsf_metadata.get(base_q, {})
        internal_type = qsf_info.get('internal_type', 'unknown')

        # Initialize question if new
        if base_q not in questions:
            q_text = qsf_info.get('text', '')
            if not q_text:
                q_text = clean_question_text(col_text)

            questions[base_q] = {
                'id': base_q,
                'text': q_text,
                'type': 'single',
                'internal_type': internal_type,
                'qsf_type': qsf_info.get('qsf_type', ''),
                'selector': qsf_info.get('selector', ''),
                'columns': [],
                'rows': {},
                'col_headers': {},
                'col_order': [],
                'qsf_info': qsf_info,  # Store for label lookups
            }

        q = questions[base_q]

        # Track all columns for this question
        if col_id not in q['columns']:
            q['columns'].append(col_id)

        # --- Determine structure based on QSF type and column pattern ---

        if internal_type in ('matrix_text', 'matrix_likert', 'matrix_multi'):
            q['type'] = 'matrix'

            if sub1 and sub2:
                # Full matrix cell: row × column
                row_label = get_label_from_qsf(qsf_info, sub1, 'choice')
                col_label = get_label_from_qsf(qsf_info, sub2, 'answer')

                if sub1 not in q['rows']:
                    q['rows'][sub1] = {'label': row_label, 'cells': {}}

                q['rows'][sub1]['cells'][sub2] = {
                    'id': col_id,
                    'col_label': col_label
                }

                if sub2 not in q['col_headers']:
                    q['col_headers'][sub2] = col_label
                    q['col_order'].append(sub2)

            elif sub1:
                # Matrix with a single column (text entry per row)
                row_label = get_label_from_qsf(qsf_info, sub1, 'choice')
                if sub1 not in q['rows']:
                    q['rows'][sub1] = {'id': col_id, 'label': row_label}

        elif internal_type == 'form':
            q['type'] = 'form'
            if sub1:
                item_label = get_label_from_qsf(qsf_info, sub1, 'choice')
                if sub1 not in q['rows']:
                    q['rows'][sub1] = {'id': col_id, 'label': item_label}

        elif internal_type in ('single_choice', 'multi_choice'):
            if sub1:
                q['type'] = 'choice'
                item_label = get_label_from_qsf(qsf_info, sub1, 'choice')
                if sub1 not in q['rows']:
                    q['rows'][sub1] = {'id': col_id, 'label': item_label}

        else:
            # Unknown type - infer from a column pattern
            if sub1 and sub2:
                q['type'] = 'matrix'

                # Try QSF labels first, fall back to CSV
                row_label = get_label_from_qsf(qsf_info, sub1, 'choice')
                col_label = get_label_from_qsf(qsf_info, sub2, 'answer')

                # If generic labels, try CSV text extraction
                if row_label == f"Row {sub1}" or col_label == f"Column {sub2}":
                    _, row_csv, col_csv = extract_matrix_labels(col_text)
                    if row_label == f"Row {sub1}" and row_csv:
                        row_label = row_csv
                    if col_label == f"Column {sub2}" and col_csv:
                        col_label = col_csv

                if sub1 not in q['rows']:
                    q['rows'][sub1] = {'label': row_label, 'cells': {}}

                q['rows'][sub1]['cells'][sub2] = {
                    'id': col_id,
                    'col_label': col_label
                }

                if sub2 not in q['col_headers']:
                    q['col_headers'][sub2] = col_label
                    q['col_order'].append(sub2)

            elif sub1:
                q['type'] = 'grouped'

                item_label = get_label_from_qsf(qsf_info, sub1, 'choice')

                # If generic, try CSV
                if item_label == f"Row {sub1}":
                    _, item_csv, _ = extract_matrix_labels(col_text)
                    if item_csv:
                        item_label = item_csv

                if sub1 not in q['rows']:
                    q['rows'][sub1] = {'id': col_id, 'label': item_label}

    logger.info(f"Parsed {len(questions)} questions with QSF metadata")
    return questions


def extract_questions_from_csv(csv_path):
    """
    Extract question structure from CSV headers only (fallback without QSF).

    Uses pattern inference for question types when QSF is not available.

    Args:
        csv_path: Path to Qualtrics CSV export.

    Returns:
        Dictionary mapping question IDs to question metadata.
    """
    logger.info("Extracting questions from CSV (no QSF)...")

    df_header = read_csv_safe(csv_path, nrows=2)
    columns = df_header.columns.tolist()
    texts = df_header.iloc[0].tolist()

    questions = {}

    for col_id, col_text in zip(columns, texts):
        # Skip metadata and timing columns
        if col_id in METADATA_COLUMNS:
            continue
        if is_timing_column(col_id):
            continue
        if not col_id.startswith('Q'):
            continue

        # Parse column ID pattern
        match = re.match(r'Q(\d+)(?:_(\d+))?(?:_(\d+))?(_TEXT)?', col_id)
        if not match:
            continue

        base_q = f"Q{match.group(1)}"
        sub1 = match.group(2)
        sub2 = match.group(3)
        is_text_field = match.group(4) is not None

        # Skip _TEXT fields for grouped questions
        if is_text_field and sub1:
            continue

        # Extract labels from column text
        base_text, row_label, col_label = extract_matrix_labels(col_text)

        # Initialize question if new
        if base_q not in questions:
            questions[base_q] = {
                'id': base_q,
                'text': '',
                'type': 'single',
                'internal_type': 'unknown',
                'columns': [],
                'rows': {},
                'col_headers': {},
                'col_order': [],
            }

        q = questions[base_q]

        # Keep the longest question text
        if len(base_text) > len(q['text']):
            q['text'] = base_text

        # Track column
        if col_id not in q['columns']:
            q['columns'].append(col_id)

        # Determine structure from column pattern
        if sub1 and sub2:
            q['type'] = 'matrix'

            row_label = row_label or f"Row {sub1}"
            col_label = col_label or f"Column {sub2}"

            if sub1 not in q['rows']:
                q['rows'][sub1] = {'label': row_label, 'cells': {}}
            elif row_label != f"Row {sub1}":
                q['rows'][sub1]['label'] = row_label

            q['rows'][sub1]['cells'][sub2] = {
                'id': col_id,
                'col_label': col_label
            }

            if sub2 not in q['col_headers']:
                q['col_headers'][sub2] = col_label
                q['col_order'].append(sub2)
            elif col_label != f"Column {sub2}":
                q['col_headers'][sub2] = col_label

        elif sub1:
            q['type'] = 'grouped'
            item_label = row_label or f"Item {sub1}"

            if sub1 not in q['rows']:
                q['rows'][sub1] = {'id': col_id, 'label': item_label}

    logger.info(f"Parsed {len(questions)} questions from CSV")
    return questions


# =============================================================================
# RESPONDENT HANDLING
# =============================================================================

def get_respondent_info(row, index=0):
    """
    Extract respondent identification info from the response row.

    Tries multiple fields to find the best identifier for the respondent.

    Args:
        row: Dictionary or Series with response data.
        index: Row index for fallback naming.

    Returns:
        Dictionary with 'name', 'id', and 'email' keys.
    """
    # Handle both dict and Series access
    def get_val(key):
        if isinstance(row, dict):
            return safe_str(row.get(key, ''))
        return safe_str(row.get(key, ''))

    first = get_val('RecipientFirstName')
    last = get_val('RecipientLastName')
    email = get_val('RecipientEmail')
    resp_id = get_val('ResponseId')
    ext_ref = get_val('ExternalReference')

    # Determine the display name (priority: name > email > ref > id > anonymous)
    if first:
        name = f"{first} {last}".strip()
    elif email:
        name = email
    elif ext_ref:
        name = ext_ref
    elif resp_id:
        name = resp_id
    else:
        name = f"Anonymous #{index + 1}"

    return {
        'name': name,
        'id': resp_id,
        'email': email,
    }


def format_respondent_header(info, show_meta=True):
    """
    Format respondent header HTML with name and optional metadata.

    Args:
        info: Dictionary from get_respondent_info().
        show_meta: Whether to include response ID.

    Returns:
        HTML string for the respondent header.
    """
    html = f"<span class='respondent-name-main'>{safe_html(info['name'])}</span>"

    if show_meta and info['id']:
        html += (
            f"<span class='respondent-meta'>"
            f"Response: {safe_html(info['id'])}</span>"
        )

    return html


def has_response(question, row):
    """
    Check if the respondent answered this question.

    Args:
        question: Question metadata dictionary.
        row: Response row (dict or Series).

    Returns:
        True if any column for this question has a non-empty value.
    """
    for col in question['columns']:
        if isinstance(row, dict):
            val = row.get(col)
        else:
            val = row.get(col)
        if not is_empty(val):
            return True
    return False


# =============================================================================
# RESPONSE FORMATTING
# =============================================================================

def format_matrix_response(question, row):
    """
    Format matrix question response as an HTML table.

    Creates a grid with row labels on the left and column headers on top.

    Args:
        question: Question metadata dictionary.
        row: Response row (dict).

    Returns:
        HTML string with matrix table or empty indicator.
    """
    q_text = question['text']
    rows_data = question['rows']
    col_order = question['col_order']
    col_headers = question['col_headers']

    # Check if this is a matrix with cells structure or simple row structure
    has_cells = any('cells' in row_info for row_info in rows_data.values())

    if has_cells:
        # Standard matrix with rows and columns
        has_data = False
        for row_info in rows_data.values():
            if 'cells' in row_info:
                for cell in row_info['cells'].values():
                    if not is_empty(row.get(cell['id'])):
                        has_data = True
                        break
            if has_data:
                break

        if not has_data:
            return format_empty()

        # Build table HTML
        html_parts = ["<table class='matrix-table'><thead><tr><th></th>"]
        for col_key in col_order:
            label = safe_html(col_headers.get(col_key, f'Col {col_key}'))
            html_parts.append(f"<th>{label}</th>")
        html_parts.append("</tr></thead><tbody>")

        # Add rows in sorted order
        sorted_rows = sorted(rows_data.keys(), key=sort_row_key)

        for row_key in sorted_rows:
            row_info = rows_data[row_key]
            if 'cells' not in row_info:
                continue

            html_parts.append(
                f"<tr><th class='row-header'>{safe_html(row_info['label'])}</th>"
            )

            for col_key in col_order:
                cell = row_info['cells'].get(col_key)
                if cell is None:
                    html_parts.append("<td class='empty-cell'>—</td>")
                else:
                    value = row.get(cell['id'])
                    if is_empty(value):
                        html_parts.append("<td class='empty-cell'>—</td>")
                    else:
                        formatted = format_value(value, q_text, cell['id'])
                        html_parts.append(f"<td>{formatted}</td>")

            html_parts.append("</tr>")

        html_parts.append("</tbody></table>")
        return ''.join(html_parts)

    else:
        # Matrix with a single column per row - use a grouped format
        return format_grouped_response(question, row)


def format_form_response(question, row):
    """
    Format form question response (TE|FORM type).

    Forms are always displayed as key-value pairs since each field
    contains unique data entry (names, emails, dates, etc.).

    Args:
        question: Question metadata dictionary.
        row: Response row (dict).

    Returns:
        HTML table with label-value pairs.
    """
    q_text = question['text']
    rows_data = question['rows']
    qsf_info = question.get('qsf_info', {})

    items = []

    # If rows_data is empty, but we have columns, create items from columns
    if not rows_data and question['columns']:
        for col_id in question['columns']:
            value = row.get(col_id)
            if is_empty(value):
                continue

            # Try to extract the label from column ID
            match = re.match(r'Q\d+_(\d+)', col_id)
            if match:
                sub_id = match.group(1)
                label = get_label_from_qsf(qsf_info, sub_id, 'choice')
            else:
                label = col_id

            items.append({
                'label': label,
                'value': safe_str(value),
                'col_id': col_id
            })
    else:
        # Use defined rows
        sorted_rows = sorted(rows_data.keys(), key=sort_row_key)

        for row_key in sorted_rows:
            row_info = rows_data[row_key]
            col_id = row_info.get('id', '')
            value = row.get(col_id)

            if is_empty(value):
                continue

            items.append({
                'label': row_info['label'],
                'value': safe_str(value),
                'col_id': col_id
            })

    if not items:
        return format_empty()

    # Build key-value table
    html_parts = ["<table class='data-table'><tbody>"]
    for item in items:
        formatted_value = format_value(item['value'], q_text, item['col_id'])
        html_parts.append(
            f"<tr><th class='data-label'>{safe_html(item['label'])}</th>"
            f"<td class='data-value'>{formatted_value}</td></tr>"
        )
    html_parts.append("</tbody></table>")

    return ''.join(html_parts)


def format_grouped_response(question, row):
    """
    Format grouped question response with intelligent display selection.

    Analyzes values to choose between data table (for numeric/unique data)
    and checkmark table (for categorical selections).

    Args:
        question: Question metadata dictionary.
        row: Response row (dict).

    Returns:
        HTML string with appropriate formatting.
    """
    q_text = question['text']
    rows_data = question['rows']
    qsf_info = question.get('qsf_info', {})

    # Handle questions without row definitions
    if not rows_data and question['columns']:
        values = []
        for col_id in question['columns']:
            val = row.get(col_id)
            if not is_empty(val):
                values.append((col_id, safe_str(val)))

        if not values:
            return format_empty()

        if len(values) == 1:
            return format_value(values[0][1], q_text, values[0][0])

        # Multiple values - show as a list
        html_parts = ["<ul class='vertical-list'>"]
        for col_id, val in values:
            html_parts.append(f"<li>{format_value(val, q_text, col_id)}</li>")
        html_parts.append("</ul>")
        return ''.join(html_parts)

    # Collect non-empty items
    items = []
    all_values = []
    sorted_rows = sorted(rows_data.keys(), key=sort_row_key)

    for row_key in sorted_rows:
        row_info = rows_data[row_key]
        col_id = row_info.get('id', '')
        value = row.get(col_id)

        if is_empty(value):
            continue

        label = row_info['label']
        val_str = safe_str(value)
        all_values.append(val_str)

        items.append({
            'label': label,
            'value': val_str,
            'col_id': col_id
        })

    if not items:
        return format_empty()

    # --- Determine a display format based on value analysis ---

    # Numeric data gets data table format
    if values_are_numeric_data(all_values):
        return _format_as_data_table(items, q_text)

    # Unique text data gets data table format
    if values_are_unique_data(all_values):
        return _format_as_data_table(items, q_text)

    # Analyze for a categorical/selection pattern
    all_selections = set()
    is_multiselect_pattern = True

    for item in items:
        val_str = item['value']

        if ',' in val_str and not is_coordinate(val_str):
            # Potential multi-select value
            parts = [p.strip() for p in val_str.split(',') if p.strip()]
            if all(len(p) <= SHORT_VALUE_MAX_LENGTH
                   and not is_numeric_value(p) for p in parts):
                all_selections.update(parts)
                item['selections'] = set(parts)
                item['is_categorical'] = True
            else:
                is_multiselect_pattern = False
                item['selections'] = None
                item['is_categorical'] = False
        elif (len(val_str) <= SHORT_VALUE_MAX_LENGTH
              and not is_numeric_value(val_str)):
            # Short text - likely categorical
            all_selections.add(val_str)
            item['selections'] = {val_str}
            item['is_categorical'] = True
        else:
            is_multiselect_pattern = False
            item['selections'] = None
            item['is_categorical'] = False

    # Decide on checkmark table vs. data table
    unique_ratio = len(all_selections) / len(items) if items else 1

    use_checkmark_table = (
		    2 <= len(all_selections) <= 10
		    and unique_ratio <= 0.7
		    and is_multiselect_pattern
		    and all(item.get('is_categorical', False) for item in items)
    )

    if use_checkmark_table:
        return _format_as_checkmark_table(items, all_selections)
    else:
        return _format_as_data_table(items, q_text)


def _format_as_data_table(items, q_text):
    """Format items as a simple key-value data table."""
    html_parts = ["<table class='data-table'><tbody>"]
    for item in items:
        formatted_value = format_value(item['value'], q_text, item['col_id'])
        html_parts.append(
            f"<tr><th class='data-label'>{safe_html(item['label'])}</th>"
            f"<td class='data-value'>{formatted_value}</td></tr>"
        )
    html_parts.append("</tbody></table>")
    return ''.join(html_parts)


def _format_as_checkmark_table(items, all_selections):
    """Format items as a checkmark matrix table for categorical selections."""
    col_order = sorted(all_selections)

    html_parts = ["<table class='grouped-table'><thead><tr><th></th>"]
    for col in col_order:
        html_parts.append(f"<th>{safe_html(col)}</th>")
    html_parts.append("</tr></thead><tbody>")

    for item in items:
        html_parts.append(
            f"<tr><th class='row-header'>{safe_html(item['label'])}</th>"
        )
        for col in col_order:
            if item.get('selections') and col in item['selections']:
                html_parts.append("<td class='cell-yes'>✓</td>")
            else:
                html_parts.append("<td class='cell-no'>—</td>")
        html_parts.append("</tr>")

    html_parts.append("</tbody></table>")
    return ''.join(html_parts)


def format_single_response(question, row):
    """Format a single question response."""
    col_id = question['columns'][0] if question['columns'] else None
    if not col_id:
        return format_empty()

    value = row.get(col_id)
    q_text = question['text']

    if is_empty(value):
        return format_empty()

    return format_value(value, q_text, col_id)


# =============================================================================
# HTML GENERATION
# =============================================================================

def generate_css():
    """
    Generate a complete CSS stylesheet for an HTML report.

    Returns responsive, colorblind-friendly CSS with print support.
    """
    return f"""
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
                         Roboto, sans-serif;
            background: linear-gradient(135deg, {COLORS['primary']} 0%,
                         {COLORS['primary_dark']} 100%);
            padding: 20px;
            min-height: 100vh;
        }}

        .container {{ max-width: 1200px; margin: 0 auto; }}

        .header {{
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }}

        .header h1 {{
            color: #2d3748;
            font-size: 28px;
            margin-bottom: 10px;
        }}

        .header .meta {{ color: {COLORS['neutral']}; font-size: 14px; }}

        .header .debug-info {{
            background: #e3f2fd;
            border: 1px solid {COLORS['primary']};
            border-radius: 6px;
            padding: 12px 15px;
            margin-top: 15px;
            color: {COLORS['primary_dark']};
            font-size: 12px;
            font-family: monospace;
        }}

        .summary {{
            background: white;
            padding: 20px 30px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            margin-bottom: 30px;
            display: flex;
            justify-content: space-around;
            flex-wrap: wrap;
        }}

        .summary-item {{ text-align: center; padding: 10px 20px; }}

        .summary-value {{
            font-size: 32px;
            font-weight: 700;
            color: {COLORS['primary']};
        }}

        .summary-label {{
            color: {COLORS['neutral']};
            font-size: 14px;
            margin-top: 5px;
        }}

        .question-card {{
            background: white;
            border-radius: 10px;
            padding: 30px;
            margin-bottom: 25px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}

        .question-header {{
            border-bottom: 3px solid {COLORS['primary']};
            padding-bottom: 15px;
            margin-bottom: 20px;
        }}

        .question-number {{
            color: {COLORS['primary']};
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}

        .question-text {{
            color: #1a202c;
            font-size: 18px;
            font-weight: 600;
            line-height: 1.5;
        }}

        .question-meta {{
            color: #a0aec0;
            font-size: 11px;
            font-family: monospace;
            margin-bottom: 8px;
        }}

        .responses-list {{ margin-top: 15px; }}

        .respondent-row {{
            padding: 15px;
            margin-bottom: 10px;
            background: {COLORS['light']};
            border-radius: 6px;
            border-left: 4px solid {COLORS['primary']};
        }}

        .respondent-row:last-child {{ margin-bottom: 0; }}
        .respondent-row.single-response {{ border-left-color: {COLORS['success']}; }}

        .respondent-name {{ margin-bottom: 8px; }}

        .respondent-name-main {{
            font-weight: 700;
            color: {COLORS['primary']};
            font-size: 14px;
        }}

        .respondent-meta {{
            display: block;
            font-size: 11px;
            color: {COLORS['neutral']};
            font-family: monospace;
            margin-top: 2px;
        }}

        .respondent-answer {{
            color: #4a5568;
            font-size: 15px;
            line-height: 1.6;
        }}

        .no-response {{ color: #a0aec0; font-style: italic; }}

        .no-responses {{
            color: #a0aec0;
            font-style: italic;
            padding: 15px;
            background: {COLORS['light']};
            border-radius: 6px;
            text-align: center;
        }}

        .code-value {{
            color: {COLORS['warning_dark']};
            font-style: italic;
            font-size: 13px;
        }}

        .data-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 8px;
        }}

        .data-table th.data-label {{
            text-align: left;
            padding: 8px 15px 8px 0;
            font-weight: 600;
            color: #2d3748;
            border-bottom: 1px solid {COLORS['border']};
            width: 40%;
            vertical-align: top;
        }}

        .data-table td.data-value {{
            text-align: left;
            padding: 8px 0;
            color: #4a5568;
            border-bottom: 1px solid {COLORS['border']};
            vertical-align: top;
        }}

        .data-table tr:last-child th,
        .data-table tr:last-child td {{
            border-bottom: none;
        }}

        .grouped-table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 6px;
            overflow: hidden;
            margin-top: 8px;
            font-size: 14px;
            border: 1px solid {COLORS['border']};
        }}

        .grouped-table thead th {{
            background: {COLORS['success']};
            color: white;
            padding: 10px 15px;
            text-align: center;
            font-weight: 600;
            border: 1px solid #007766;
            font-size: 13px;
        }}

        .grouped-table thead th:first-child {{
            background: {COLORS['primary']};
            text-align: left;
            border-color: #005588;
        }}

        .grouped-table tbody th.row-header {{
            background: #edf2f7;
            color: #2d3748;
            padding: 10px 15px;
            text-align: left;
            font-weight: 500;
            border: 1px solid {COLORS['border']};
            min-width: 180px;
        }}

        .grouped-table td {{
            padding: 10px 15px;
            text-align: center;
            border: 1px solid {COLORS['border']};
            font-size: 16px;
        }}

        .grouped-table td.cell-yes {{
            color: {COLORS['success']};
            font-weight: 700;
        }}

        .grouped-table td.cell-no {{ color: #cbd5e0; }}

        .grouped-table tbody tr:nth-child(even) {{ background: #f7fafc; }}

        .vertical-list {{ list-style: none; padding: 0; margin: 0; }}

        .vertical-list li {{
            padding: 6px 0 6px 20px;
            position: relative;
            border-bottom: 1px solid #edf2f7;
        }}

        .vertical-list li:last-child {{ border-bottom: none; }}

        .vertical-list li::before {{
            content: "•";
            color: {COLORS['primary']};
            font-weight: bold;
            position: absolute;
            left: 0;
        }}

        .matrix-table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 6px;
            overflow: hidden;
            margin-top: 8px;
            font-size: 14px;
            border: 1px solid {COLORS['border']};
        }}

        .matrix-table thead th {{
            background: {COLORS['primary']};
            color: white;
            padding: 12px 15px;
            text-align: center;
            font-weight: 600;
            border: 1px solid #005588;
        }}

        .matrix-table thead th:first-child {{
            background: {COLORS['primary_dark']};
            text-align: left;
        }}

        .matrix-table tbody th.row-header {{
            background: #edf2f7;
            color: #2d3748;
            padding: 12px 15px;
            text-align: left;
            font-weight: 600;
            border: 1px solid {COLORS['border']};
            min-width: 150px;
        }}

        .matrix-table td {{
            padding: 12px 15px;
            text-align: center;
            border: 1px solid {COLORS['border']};
        }}

        .matrix-table td.empty-cell {{ color: #cbd5e0; }}
        .matrix-table tbody tr:nth-child(even) {{ background: #f7fafc; }}

        .url-link {{
            color: {COLORS['primary']};
            text-decoration: none;
            word-break: break-all;
        }}

        .url-link:hover {{ text-decoration: underline; }}

        .file-upload {{
            background: #f0f4f8;
            padding: 8px 12px;
            border-radius: 6px;
            display: inline-block;
        }}

        .file-upload a {{ color: {COLORS['primary']}; text-decoration: none; }}

        .coordinate {{
            background: #fff3e0;
            color: #b35500;
            padding: 4px 8px;
            border-radius: 4px;
            font-family: monospace;
        }}

        .timing {{ color: {COLORS['neutral']}; font-size: 13px; }}

        .long-text {{
            background: {COLORS['light']};
            padding: 15px;
            border-radius: 6px;
            border-left: 3px solid {COLORS['primary']};
        }}

        .long-text p {{ margin-bottom: 10px; }}
        .long-text p:last-child {{ margin-bottom: 0; }}

        .drill-down {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 4px;
        }}

        .drill-level {{
            background: #e3f2fd;
            padding: 4px 8px;
            border-radius: 4px;
        }}

        .drill-arrow {{ color: {COLORS['primary']}; font-weight: bold; }}

        .json-data {{
            background: #1a202c;
            color: #e2e8f0;
            padding: 15px;
            border-radius: 6px;
            overflow-x: auto;
            font-size: 12px;
        }}

        @media (max-width: 768px) {{
            body {{ padding: 10px; }}
            .question-card {{ padding: 20px; }}
            .matrix-table, .grouped-table {{ font-size: 12px; }}

            .matrix-table thead th,
            .matrix-table tbody th.row-header,
            .matrix-table td {{ padding: 8px; }}

            .grouped-table thead th,
            .grouped-table tbody th.row-header,
            .grouped-table td {{ padding: 6px 8px; }}

            .summary {{ flex-direction: column; }}
            .data-table th.data-label {{ width: 50%; }}
        }}

        @media print {{
            body {{ background: white; padding: 0; }}

            .question-card {{
                box-shadow: none;
                border: 1px solid {COLORS['border']};
                page-break-inside: avoid;
            }}

            .debug-info, .question-meta {{ display: none; }}
        }}
    """


def generate_html(questions, respondents, debug_mode=False, has_qsf=False):
    """
    Generate a complete HTML report from parsed questions and responses.

    Args:
        questions: Dictionary of question metadata.
        respondents: List of respondent dictionaries.
        debug_mode: Whether to include debug information.
        has_qsf: Whether QSF metadata was used.

    Returns:
        Complete HTML document string.
    """
    timestamp = datetime.now().strftime('%B %d, %Y at %I:%M %p')

    # Count question types for summary
    type_counts = {}
    for q in questions.values():
        qtype = q.get('type', 'single')
        type_counts[qtype] = type_counts.get(qtype, 0) + 1

    # Build debug info block
    debug = ""
    if debug_mode:
        type_str = ', '.join(f"{k}: {v}" for k, v in sorted(type_counts.items()))
        qsf_status = "✓ QSF metadata" if has_qsf else "CSV inference"
        debug = f"""
            <div class="debug-info">
                <strong>🔧 Debug:</strong>
                Questions: {len(questions)} ({type_str}) |
                Respondents: {len(respondents)} |
                Source: {qsf_status}
            </div>
        """

    # Build HTML document
    html_parts = [f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Qualtrics Survey Report</title>
    <style>{generate_css()}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 Qualtrics Survey Report</h1>
            <div class="meta">Generated on {timestamp}</div>
            {debug}
        </div>

        <div class="summary">
            <div class="summary-item">
                <div class="summary-value">{len(respondents)}</div>
                <div class="summary-label">Respondents</div>
            </div>
            <div class="summary-item">
                <div class="summary-value">{len(questions)}</div>
                <div class="summary-label">Questions</div>
            </div>
            <div class="summary-item">
                <div class="summary-value">{type_counts.get('matrix', 0)}</div>
                <div class="summary-label">Matrix Questions</div>
            </div>
        </div>
"""]

    # Sort questions by numeric ID
    sorted_qs = sorted(
        questions.items(),
        key=lambda x: int(re.search(r'Q(\d+)', x[0]).group(1))
    )

    # Generate question cards
    for q_id, question in sorted_qs:
        answered = [r for r in respondents if has_response(question, r['row'])]

        # Debug metadata for question
        meta = ""
        if debug_mode:
            internal = question.get('internal_type', 'unknown')
            meta = (
                f"<div class='question-meta'>type: {question['type']} | "
                f"internal: {internal} | "
                f"cols: {len(question['columns'])} | "
                f"responses: {len(answered)}</div>"
            )

        html_parts.append(f"""
        <div class="question-card">
            <div class="question-header">
                <div class="question-number">{q_id}</div>
                {meta}
                <div class="question-text">{safe_html(question['text'])}</div>
            </div>
            <div class="responses-list">
""")

        if not answered:
            html_parts.append('<div class="no-responses">No responses</div>')
        else:
            single_class = " single-response" if len(answered) == 1 else ""

            for resp in answered:
                header = format_respondent_header(resp['info'])

                # Choose a formatter based on a question type
                qtype = question['type']
                internal_type = question.get('internal_type', '')

                if qtype == 'matrix':
                    answer = format_matrix_response(question, resp['row'])
                elif qtype == 'form' or internal_type == 'form':
                    answer = format_form_response(question, resp['row'])
                elif qtype in ('grouped', 'choice'):
                    answer = format_grouped_response(question, resp['row'])
                elif qtype == 'single':
                    if len(question['columns']) == 1:
                        answer = format_single_response(question, resp['row'])
                    else:
                        answer = format_grouped_response(question, resp['row'])
                else:
                    answer = format_single_response(question, resp['row'])

                html_parts.append(f"""
                <div class="respondent-row{single_class}">
                    <div class="respondent-name">{header}</div>
                    <div class="respondent-answer">{answer}</div>
                </div>
""")

        html_parts.append("""
            </div>
        </div>
""")

    # Close document
    html_parts.append("""
    </div>
</body>
</html>
""")

    return ''.join(html_parts)


# =============================================================================
# MAIN PROCESSING
# =============================================================================

def process_qualtrics(csv_path, output_html='qualtrics_report.html',
                      qsf_path=None, progress_callback=None, debug_mode=False):
    """
    Process Qualtrics CSV and generate an HTML report.

    Main entry point for report generation. Handles the complete workflow
    from parsing to HTML output.

    Args:
        csv_path: Path to a Qualtrics CSV export file.
        output_html: Output HTML file path.
        qsf_path: Optional path to QSF file for accurate question types.
        progress_callback: Optional function for progress updates (GUI).
        debug_mode: Include debug information in output.

    Returns:
        Tuple of (respondent_count, question_count).
    """
    logger.info(f"Processing: {csv_path}")

    # --- Validate input ---
    if progress_callback:
        progress_callback("Validating CSV...")
    validate_csv(csv_path)

    # --- Parse QSF if provided ---
    qsf_metadata = {}
    has_qsf = False
    if qsf_path and os.path.exists(qsf_path):
        if progress_callback:
            progress_callback("Parsing QSF metadata...")
        qsf_metadata = parse_qsf(qsf_path)
        has_qsf = True

    # --- Extract question structure ---
    if progress_callback:
        progress_callback("Extracting questions...")

    if has_qsf:
        questions = extract_questions_with_qsf(csv_path, qsf_metadata)
    else:
        questions = extract_questions_from_csv(csv_path)

    # --- Read response data ---
    if progress_callback:
        progress_callback("Reading responses...")

    df = read_csv_safe(csv_path, skiprows=[1])

    # Skip ImportId row if present (Qualtrics metadata row)
    if len(df) > 0 and df.iloc[0].astype(str).str.contains('ImportId|{').any():
        df = df.iloc[1:].reset_index(drop=True)

    # --- Process respondents ---
    if progress_callback:
        progress_callback("Processing respondents...")

    # Use to_dict('records') for better performance on large datasets
    records = df.to_dict('records')
    respondents = [
        {'info': get_respondent_info(row, idx), 'row': row}
        for idx, row in enumerate(records)
    ]

    # --- Generate HTML ---
    if progress_callback:
        progress_callback("Generating report...")
    html = generate_html(questions, respondents, debug_mode, has_qsf)

    # --- Write output ---
    if progress_callback:
        progress_callback("Writing file...")
    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(html)

    logger.info(f"Complete: {len(respondents)} respondents, {len(questions)} questions")

    return len(respondents), len(questions)


# =============================================================================
# GUI (OPTIONAL)
# =============================================================================

if TKINTER_AVAILABLE:

    class QualtricsReportGeneratorGUI:
        """
        Simple GUI for the Qualtrics Report Generator.

        Provides file selection dialogs, progress indication, and
        automatic QSF detection.
        """

        def __init__(self, root):
            """Initialize the GUI."""
            self.root = root
            self.root.title("Qualtrics Report Generator")
            self.root.geometry("550x480")
            self.root.resizable(False, False)

            # Variables for form fields
            self.input_file = tk.StringVar()
            self.qsf_file = tk.StringVar()
            self.output_file = tk.StringVar()
            self.debug_mode = tk.BooleanVar(value=False)

            self._build_ui()

        def _build_ui(self):
            """Build the GUI layout."""
            frame = ttk.Frame(self.root, padding="20")
            frame.grid(row=0, column=0, sticky="nsew")

            # Title
            ttk.Label(
                frame,
                text="📋 Qualtrics Report Generator",
                font=('Helvetica', 16, 'bold')
            ).grid(row=0, column=0, columnspan=3, pady=(0, 20))

            # Input CSV
            ttk.Label(frame, text="Input CSV:").grid(row=1, column=0, sticky="w")
            ttk.Entry(
                frame, textvariable=self.input_file, width=45
            ).grid(row=2, column=0, columnspan=2, sticky="ew")
            ttk.Button(
                frame, text="Browse", command=self._browse_input
            ).grid(row=2, column=2, padx=(5, 0))

            # QSF file (optional)
            ttk.Label(
                frame, text="QSF File (optional, for accurate types):"
            ).grid(row=3, column=0, sticky="w", pady=(10, 0))
            ttk.Entry(
                frame, textvariable=self.qsf_file, width=45
            ).grid(row=4, column=0, columnspan=2, sticky="ew")
            ttk.Button(
                frame, text="Browse", command=self._browse_qsf
            ).grid(row=4, column=2, padx=(5, 0))

            # Output HTML
            ttk.Label(
                frame, text="Output HTML:"
            ).grid(row=5, column=0, sticky="w", pady=(10, 0))
            ttk.Entry(
                frame, textvariable=self.output_file, width=45
            ).grid(row=6, column=0, columnspan=2, sticky="ew")
            ttk.Button(
                frame, text="Browse", command=self._browse_output
            ).grid(row=6, column=2, padx=(5, 0))

            # Debug option
            ttk.Checkbutton(
                frame, text="Include debug info", variable=self.debug_mode
            ).grid(row=7, column=0, sticky="w", pady=(15, 0))

            # Progress bar
            self.progress = ttk.Progressbar(
                frame, mode='indeterminate', length=350
            )
            self.progress.grid(
                row=8, column=0, columnspan=3, pady=(20, 5), sticky="ew"
            )

            # Status label
            self.status = ttk.Label(frame, text="Ready", foreground='gray')
            self.status.grid(row=9, column=0, columnspan=3)

            # Generate button
            self.btn = ttk.Button(
                frame, text="Generate Report", command=self._generate
            )
            self.btn.grid(row=10, column=0, columnspan=3, pady=(15, 0))

        def _browse_input(self):
            """Handle input file selection."""
            path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
            if path:
                self.input_file.set(path)

                # Auto-set output filename
                if not self.output_file.get():
                    self.output_file.set(
                        os.path.splitext(path)[0] + '_report.html'
                    )

                # Auto-detect QSF in same directory
                qsf_path = os.path.splitext(path)[0] + '.qsf'
                if os.path.exists(qsf_path) and not self.qsf_file.get():
                    self.qsf_file.set(qsf_path)

        def _browse_qsf(self):
            """Handle QSF file selection."""
            path = filedialog.askopenfilename(filetypes=[("QSF", "*.qsf")])
            if path:
                self.qsf_file.set(path)

        def _browse_output(self):
            """Handle output file selection."""
            path = filedialog.asksaveasfilename(
                defaultextension=".html",
                filetypes=[("HTML", "*.html")]
            )
            if path:
                self.output_file.set(path)

        def _update_status(self, msg):
            """Update status label."""
            self.status.config(text=msg)
            self.root.update_idletasks()

        def _generate(self):
            """Handle generate button click."""
            # Validate input
            if not self.input_file.get():
                messagebox.showerror("Error", "Please select an input CSV file")
                return

            if not os.path.exists(self.input_file.get()):
                messagebox.showerror("Error", "Input file not found")
                return

            # Disable the button and start progress
            self.btn.config(state='disabled')
            self.progress.start(10)

            try:
                qsf = self.qsf_file.get() if self.qsf_file.get() else None
                n_resp, n_q = process_qualtrics(
                    self.input_file.get(),
                    self.output_file.get() or 'qualtrics_report.html',
                    qsf_path=qsf,
                    progress_callback=self._update_status,
                    debug_mode=self.debug_mode.get()
                )

                self.progress.stop()
                self._update_status("✅ Complete!")

                # Show a success message
                qsf_msg = " (with QSF metadata)" if qsf else " (CSV inference)"
                msg = (
                    f"Report generated{qsf_msg}!\n\n"
                    f"Respondents: {n_resp}\n"
                    f"Questions: {n_q}"
                )

                if messagebox.askyesno("Success", msg + "\n\nOpen file?"):
                    import webbrowser
                    webbrowser.open(
                        'file://' + os.path.abspath(self.output_file.get())
                    )

            except Exception as e:
                self.progress.stop()
                self._update_status("❌ Error")
                logger.exception("Processing failed")
                messagebox.showerror("Error", str(e))

            finally:
                self.btn.config(state='normal')


# =============================================================================
# COMMAND LINE INTERFACE
# =============================================================================

def main():
    """
    Main entry point for command line and GUI usage.

    If called with arguments, runs in CLI mode.
    If called without arguments, launches GUI (if available).
    """
    if len(sys.argv) > 1:
        # --- CLI mode ---
        args = sys.argv[1:]
        csv_path = None
        qsf_path = None
        output = 'qualtrics_report.html'
        debug = False
        log_file = None

        # Parse arguments
        i = 0
        while i < len(args):
            arg = args[i]

            if arg in ('-d', '--debug'):
                debug = True
            elif arg in ('-q', '--qsf'):
                if i + 1 < len(args):
                    qsf_path = args[i + 1]
                    i += 1
            elif arg in ('-l', '--log'):
                log_file = args[i + 1] if i + 1 < len(args) else 'debug.log'
                i += 1
            elif arg in ('-o', '--output'):
                if i + 1 < len(args):
                    output = args[i + 1]
                    i += 1
            elif arg in ('-h', '--help'):
                print("Qualtrics Report Generator")
                print("")
                print("Usage: python qualtrics_report_generator.py [options] input.csv")
                print("")
                print("Options:")
                print("  -q, --qsf FILE    QSF file for accurate question types")
                print("  -o, --output FILE Output HTML file (default: qualtrics_report.html)")
                print("  -d, --debug       Include debug info in report")
                print("  -l, --log FILE    Write debug log to file")
                print("  -h, --help        Show this help message")
                print("")
                print("Examples:")
                print("  python qualtrics_report_generator.py survey.csv")
                print("  python qualtrics_report_generator.py -q survey.qsf survey.csv")
                print("  python qualtrics_report_generator.py -q survey.qsf -o report.html survey.csv")
                sys.exit(0)
            elif not arg.startswith('-'):
                if csv_path is None:
                    csv_path = arg

            i += 1

        # Validate required argument
        if not csv_path:
            print("Error: No input CSV file specified")
            print("Run with --help for usage information")
            sys.exit(1)

        # Setup logging and process
        setup_logging(debug, log_file)

        try:
            n_resp, n_q = process_qualtrics(
                csv_path, output,
                qsf_path=qsf_path,
                debug_mode=debug
            )
            qsf_msg = " (with QSF)" if qsf_path else ""
            print(f"\n✅ Generated{qsf_msg}: {output}")
            print(f"   Respondents: {n_resp}")
            print(f"   Questions: {n_q}")
        except Exception as e:
            logger.exception("Failed")
            print(f"❌ Error: {e}")
            sys.exit(1)

    else:
        # --- GUI mode ---
        if TKINTER_AVAILABLE:
            root = tk.Tk()
            QualtricsReportGeneratorGUI(root)
            root.mainloop()
        else:
            print("Qualtrics Report Generator")
            print("")
            print("GUI requires tkinter. Use CLI instead:")
            print("  python qualtrics_report_generator.py [options] input.csv")
            print("")
            print("Run with --help for all options.")
            sys.exit(1)


if __name__ == "__main__":
    main()
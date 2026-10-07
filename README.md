# CampusPulse

Smart notice reader: TL;DR, deadline calendar, footnote-deadline alerts, and Q&A with verified source quotes.

## Run
```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then paste your Gemini key (aistudio.google.com/apikey)
streamlit run app.py
```
Try the files in `sample_circulars/`.

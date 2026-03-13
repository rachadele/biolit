from pubmed_screener.utils import read_eml_body
import re

body = read_eml_body('tests/fixtures/test1.eml')
print("Body length:", len(body))
print("First 200 chars:", repr(body[:200]))
idx = body.find('pmid')
print("First 'pmid' at index:", idx)
if idx >= 0:
    print("Context:", repr(body[idx:idx+100]))

# Try various patterns
for pat in [r'PMID:\s*(\d+)', r'docsum-pmid[^>]*>(\d+)<', r'/(\d{7,8})/', r'article_id=(\d+)']:
    found = re.findall(pat, body)
    print(f"Pattern {pat!r}: {found[:5]}")


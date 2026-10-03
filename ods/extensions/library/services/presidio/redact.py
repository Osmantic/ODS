"""Explicit local text-file redaction using the two installed Presidio APIs."""
import argparse
import json
import pathlib
import urllib.error
import urllib.request


def post(endpoint, body):
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=pathlib.Path)
    parser.add_argument('--analyzer', default='http://localhost:11020')
    parser.add_argument('--anonymizer', default='http://localhost:11021')
    parser.add_argument('--language', default='en')
    args = parser.parse_args()
    try:
        original = args.file.read_text(encoding='utf-8')
        entities = post(args.analyzer.rstrip('/') + '/analyze', {'text': original, 'language': args.language})
        if not isinstance(entities, list):
            raise ValueError('Analyzer did not return an entity list')
        result = post(args.anonymizer.rstrip('/') + '/anonymize', {'text': original, 'analyzer_results': entities, 'anonymizers': {'DEFAULT': {'type': 'replace', 'new_value': '[REDACTED]'}}})
        if not isinstance(result, dict) or not isinstance(result.get('text'), str):
            raise ValueError('Anonymizer did not return text')
    except (OSError, UnicodeError, ValueError, urllib.error.URLError):
        parser.exit(1, 'Redaction failed. Check the file, service readiness and supported language; no result was emitted.\n')
    print(result['text'])


if __name__ == '__main__':
    main()

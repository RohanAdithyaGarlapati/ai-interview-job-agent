import json
import sys

from agent.pipeline import resolve_job_source

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python cli.py <linkedin_job_url>")
        sys.exit(1)
    result = resolve_job_source(sys.argv[1])
    print(json.dumps(result.to_dict(), indent=2))

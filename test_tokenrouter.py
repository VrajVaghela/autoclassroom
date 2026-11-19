import sys
import os
from openai import OpenAI

api_key = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TOKENROUTER_API_KEY", "")

if not api_key:
    print("Usage: python test_tokenrouter.py <YOUR_API_KEY>")
    print("Or set TOKENROUTER_API_KEY environment variable.")
    sys.exit(1)

client = OpenAI(
    base_url='https://api.tokenrouter.com/v1',
    api_key=api_key,
)

print("Sending request to TokenRouter (model: moonshotai/kimi-k3-free)...")

stream = client.chat.completions.create(
    model="moonshotai/kimi-k3-free",
    messages=[
        {"role": "system", "content": "You are an intelligent assistant, please reply concisely."},
        {"role": "user", "content": "Hello, what kind of model are you?"},
    ],
    stream=True,
    stream_options={"include_usage": True},
    extra_body={}
)

content_parts = []
for chunk in stream:
    if chunk.choices:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            content_parts.append(delta.content)

full_content = "".join(content_parts)
print("\nResponse:")
print(full_content)

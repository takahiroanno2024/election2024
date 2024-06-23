import github
from github import Github
import os
import openai
import regex as re
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

token = os.getenv("GITHUB_TOKEN")
qd_api = os.getenv("QD_API_KEY")
qd_url = os.getenv("QD_URL")
g = Github(token)
repo = g.get_repo(os.getenv("GITHUB_REPOSITORY"))
issue = repo.get_issue(int(os.getenv("GITHUB_EVENT_ISSUE_NUMBER")))
issue_content = f"{issue.title}\n{issue.body}"
try:
    repo.create_label(name="toxic", color="ff0000")
    repo.create_label(name="duplicated", color="708090")
except:
    pass

qdrant_client = QdrantClient(
    url=qd_url, 
    api_key=qd_api,
)
openai_client = openai.Client()
embedding_model = "text-embedding-3-small"
collection_name = "issue_collection"

def validate_image(text):
    model_name = "gpt-4o"
    prompt = "この画像が暴力的、もしくは性的な画像の場合trueと返してください。"
    image_url = re.search(r"!\[[^\s]+\]\((https://[^\s]+)\)", text)
    if image_url and len(image_url) > 1:
        image_url = image_url[1]
    else:
        return False
    try:
        response = openai_client.chat.completions.create(
          model=model_name,
          messages=[
            {
              "role": "user",
              "content": [
                {"type": "text", "text": prompt},
                {
                  "type": "image_url",
                  "image_url": {
                    "url": image_url
                  },
                },
              ],
            }
          ],
          max_tokens=1200,
        )
    except:
        return True
    v = response.choices[0].message.content.lower()
    if "true" in v:
        return True
    else:
        return False

def judge_violation(text):
    response = openai_client.moderations.create(input=text)
    flag = response.results[0].flagged
    video_flag = validate_image(text)
    if flag or video_flag:
        print(response)
        issue.add_to_labels("toxic")
        if video_flag:
            warn = "不適切な画像です。アカウントBANの危険性があります。"
        else:
            warn = "不適切な投稿です。アカウントBANの危険性があります。"
        issue.create_comment(warn)
        issue.edit(state="closed")
        return True
    return flag

def add_issue(text:str, iss_num:int):
    texts = [text]
    ids = [iss_num]
    result = openai_client.embeddings.create(input=texts, model=embedding_model)
    points = [
        PointStruct(
            id=idx,
            vector=data.embedding,
            payload={"text": t},
        )
        for idx, data, t in zip(ids, result.data, texts)
    ]
    qdrant_client.upsert(collection_name, points)
    return text

def merge_issue(iss:int):
    issue.add_to_labels("duplicated")
    print(f"merge to {iss}")
    issue.create_comment(f"#{iss} と重複しているかもしれません")
    return iss

def qd_search(text:str):
    results = qdrant_client.search(
        collection_name=collection_name,
        query_vector=openai_client.embeddings.create(
            input=[text],
            model=embedding_model,
        )
        .data[0]
        .embedding,
    )
    return results

def qd_add(text:str, iss_num:int):
    texts = [text]
    ids = [iss_num]
    result = openai_client.embeddings.create(input=texts, model=embedding_model)
    points = [
    PointStruct(
        id=idx,
        vector=data.embedding,
        payload={"text": text},
    )
    for idx, data, text in zip(ids, result.data, texts)
    ]
    qdrant_client.upsert(collection_name, points)

if judge_violation(issue_content):
    quit()
results = qd_search(issue_content)

if len(results) > 2:
    results = results[:3]
else:
    results = results
print(results)
res = ""
for i in results:
    res+=f'id:{i.id}\n内容:{i.payload["text"]}\n'
res = res.strip()

prompt= f"""
以下は市民から寄せられた政策提案です。
{issue_content}
この投稿を読み、以下の過去提案の中に重複する提案があるかを判断してください。
{res}
重複する提案があればそのidを出力してください。
もし存在しない場合は0と出力してください。

[出力形式]
id:0
"""
print(prompt)
completion = openai_client.chat.completions.create(
  model="gpt-4o",
  max_tokens= 1024,
  messages=[
  {"role": "system", "content": prompt},
  ]
)
review = completion.choices[0].message.content
if ":" in review:
    review = review.split(":")[-1]
if review.isdecimal():
    if review == "0":
        add_issue(issue_content, issue.number)
    else:
        merge_issue(int(review))
print(review)
import os
from typing import List, Dict, Any
import regex as re
from github import Github
from github.Issue import Issue
from github.Repository import Repository
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
import openai

# GitHub Actions環境で実行されていない場合のみ.envファイルを読み込む
if not os.getenv('GITHUB_ACTIONS'):
    from dotenv import load_dotenv
    load_dotenv()

# 定数
EMBEDDING_MODEL = "text-embedding-3-small"
COLLECTION_NAME = "issue_collection"
GPT_MODEL = "gpt-4o"
MAX_RESULTS = 3

class Config:
    def __init__(self):
        print("設定の初期化を開始します...")
        self.github_token = os.getenv("GITHUB_TOKEN")
        if self.github_token is None:
            print("GITHUB_TOKENが見つかりません ...")
        else:
            print("GITHUB_TOKENからトークンを正常に取得しました。")
        
        self.qd_api_key = os.getenv("QD_API_KEY")
        print("QD_API_KEYの状態:", "取得済み" if self.qd_api_key else "見つかりません")
        
        self.qd_url = os.getenv("QD_URL")
        print("QD_URLの状態:", "取得済み" if self.qd_url else "見つかりません")
        
        self.github_repo = os.getenv("GITHUB_REPOSITORY")
        print("GITHUB_REPOSITORYの状態:", "取得済み" if self.github_repo else "見つかりません")
        
        self.issue_number = os.getenv("GITHUB_EVENT_ISSUE_NUMBER")
        if self.issue_number:
            self.issue_number = int(self.issue_number)
            print(f"GITHUB_EVENT_ISSUE_NUMBER: {self.issue_number}")
        else:
            print("GITHUB_EVENT_ISSUE_NUMBERが見つかりません")
        print("設定の初期化が完了しました。")

class GithubHandler:
    def __init__(self, config: Config):
        self.github = Github(config.github_token)
        self.repo = self.github.get_repo(config.github_repo)
        self.issue = self.repo.get_issue(config.issue_number)

    def create_labels(self):
        """ラベルを作成する（既に存在する場合は無視）"""
        try:
            self.repo.create_label(name="toxic", color="ff0000")
            self.repo.create_label(name="duplicated", color="708090")
        except:
            pass

    def add_label(self, label: str):
        """Issueにラベルを追加する"""
        self.issue.add_to_labels(label)

    def close_issue(self):
        """Issueをクローズする"""
        self.issue.edit(state="closed")

    def add_comment(self, comment: str):
        """Issueにコメントを追加する"""
        self.issue.create_comment(comment)

class ContentModerator:
    def __init__(self, openai_client: openai.Client):
        self.openai_client = openai_client

    def is_inappropriate_image(self, text: str) -> bool:
        """画像の内容が不適切かどうかを判断する"""
        image_url = self._extract_image_url(text)
        if not image_url:
            return False

        prompt = "この画像が暴力的、もしくは性的な画像の場合trueと返してください。"
        try:
            response = self.openai_client.chat.completions.create(
                model=GPT_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                max_tokens=1200,
            )
            return "true" in response.choices[0].message.content.lower()
        except:
            return True

    def is_inappropriate_issue(self, text: str) -> bool:
        """テキストと画像の内容が不適切かどうかを判断する"""
        response = self.openai_client.moderations.create(input=text)
        return response.results[0].flagged or self.is_inappropriate_image(text)

    @staticmethod
    def _extract_image_url(text: str) -> str:
        """テキストから画像URLを抽出する"""
        match = re.search(r"!\[[^\s]+\]\((https://[^\s]+)\)", text)
        return match[1] if match and len(match) > 1 else ""

class QdrantHandler:
    def __init__(self, client: QdrantClient, openai_client: openai.Client):
        self.client = client
        self.openai_client = openai_client

    def add_issue(self, text: str, issue_number: int):
        """新しい問題をQdrantに追加する"""
        embedding = self._create_embedding(text)
        point = PointStruct(id=issue_number, vector=embedding, payload={"text": text})
        self.client.upsert(COLLECTION_NAME, [point])

    def search_similar_issues(self, text: str) -> List[Dict[str, Any]]:
        """類似の問題を検索する"""
        embedding = self._create_embedding(text)
        results = self.client.search(collection_name=COLLECTION_NAME, query_vector=embedding)
        return results[:MAX_RESULTS]

    def _create_embedding(self, text: str) -> List[float]:
        """テキストのembeddingを作成する"""
        result = self.openai_client.embeddings.create(input=[text], model=EMBEDDING_MODEL)
        return result.data[0].embedding

class IssueProcessor:
    def __init__(self, github_handler: GithubHandler, content_moderator: ContentModerator, qdrant_handler: QdrantHandler, openai_client: openai.Client):
        self.github_handler = github_handler
        self.content_moderator = content_moderator
        self.qdrant_handler = qdrant_handler
        self.openai_client = openai_client

    def process_issue(self, issue_content: str):
        """Issueを処理する"""
        if self.content_moderator.is_inappropriate_issue(issue_content):
            self._handle_violation()
            return

        similar_issues = self.qdrant_handler.search_similar_issues(issue_content)
        if not similar_issues:
            self.qdrant_handler.add_issue(issue_content, self.github_handler.issue.number)
            return

        duplicate_id = self._check_duplication(issue_content, similar_issues)
        if duplicate_id:
            self._handle_duplication(duplicate_id)
        else:
            self.qdrant_handler.add_issue(issue_content, self.github_handler.issue.number)

    def _handle_violation(self):
        """違反を処理する"""
        self.github_handler.add_label("toxic")
        self.github_handler.add_comment("不適切な投稿です。アカウントBANの危険性があります。")
        self.github_handler.close_issue()

    def _check_duplication(self, issue_content: str, similar_issues: List[Dict[str, Any]]) -> int:
        """重複をチェックする"""
        prompt = self._create_duplication_check_prompt(issue_content, similar_issues)
        completion = self.openai_client.chat.completions.create(
            model=GPT_MODEL,
            max_tokens=1024,
            messages=[{"role": "system", "content": prompt}]
        )
        review = completion.choices[0].message.content
        if ":" in review:
            review = review.split(":")[-1]
        return int(review) if review.isdecimal() and review != "0" else 0

    def _handle_duplication(self, duplicate_id: int):
        """重複を処理する"""
        self.github_handler.add_label("duplicated")
        self.github_handler.add_comment(f"#{duplicate_id} と重複しているかもしれません")

    @staticmethod
    def _create_duplication_check_prompt(issue_content: str, similar_issues: List[Dict[str, Any]]) -> str:
        """重複チェック用のプロンプトを作成する"""
        similar_issues_text = "\n".join([f'id:{issue.id}\n内容:{issue.payload["text"]}' for issue in similar_issues])
        return f"""
        以下は市民から寄せられた政策提案です。
        {issue_content}
        この投稿を読み、以下の過去提案の中に重複する提案があるかを判断してください。
        {similar_issues_text}
        重複する提案があればそのidを出力してください。
        もし存在しない場合は0と出力してください。

        [出力形式]
        id:0
        """

def setup():
    """セットアップを行い、必要なオブジェクトを返す"""
    config = Config()
    github_handler = GithubHandler(config)
    github_handler.create_labels()

    openai_client = openai.Client()
    content_moderator = ContentModerator(openai_client)

    qdrant_client = QdrantClient(url=config.qd_url, api_key=config.qd_api_key)
    qdrant_handler = QdrantHandler(qdrant_client, openai_client)

    return github_handler, content_moderator, qdrant_handler, openai_client

def main():
    github_handler, content_moderator, qdrant_handler, openai_client = setup()
    issue_processor = IssueProcessor(github_handler, content_moderator, qdrant_handler, openai_client)
    issue_content = f"{github_handler.issue.title}\n{github_handler.issue.body}"
    issue_processor.process_issue(issue_content)

if __name__ == "__main__":
    main()

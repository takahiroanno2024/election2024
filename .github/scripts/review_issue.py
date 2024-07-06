import os
from typing import List, Dict, Any
import regex as re
from github import Github
from github.Issue import Issue
from github.Repository import Repository
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
import openai
from pydantic_settings import BaseSettings
from loguru import logger

class Settings(BaseSettings):
    github_token: str
    qd_api_key: str
    qd_url: str
    github_repository: str
    github_event_issue_number: int
    embedding_model: str = "text-embedding-3-small"
    collection_name: str = "issue_collection"
    gpt_model: str = "gpt-4"
    max_results: int = 3
    openai_api_key: str

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = 'ignore'  # 定義されていない環境変数を無視

class Config:
    def __init__(self):
        logger.info("設定の初期化を開始します...")
        try:
            self.settings = Settings()
            logger.success("設定の初期化が成功しました。")
        except Exception as e:
            logger.error(f"設定の初期化に失敗しました: {e}")
            raise
            
class GithubHandler:
    def __init__(self, config: Config):
        logger.info("GitHubハンドラの初期化中...")
        self.github = Github(config.settings.github_token)
        self.repo = self.github.get_repo(config.settings.github_repository)
        self.issue = self.repo.get_issue(config.settings.github_event_issue_number)
        logger.success("GitHubハンドラの初期化が成功しました。")

    def create_labels(self):
        logger.info("ラベルの作成中...")
        try:
            self.repo.create_label(name="toxic", color="ff0000")
            self.repo.create_label(name="duplicated", color="708090")
            logger.success("ラベルの作成が成功しました。")
        except Exception as e:
            logger.warning(f"ラベルの作成に失敗しました: {e}")

    def add_label(self, label: str):
        logger.info(f"'{label}'ラベルをissueに追加中...")
        self.issue.add_to_labels(label)
        logger.success(f"'{label}'ラベルの追加が成功しました。")

    def close_issue(self):
        logger.info("issueをクローズ中...")
        self.issue.edit(state="closed")
        logger.success("issueのクローズが成功しました。")

    def add_comment(self, comment: str):
        logger.info("issueにコメントを追加中...")
        self.issue.create_comment(comment)
        logger.success("コメントの追加が成功しました。")

class ContentModerator:
    def __init__(self, openai_client: openai.Client):
        self.openai_client = openai_client

    def validate_image(self, text: str) -> bool:
        logger.info("画像コンテンツの検証中...")
        image_url = self._extract_image_url(text)
        if not image_url:
            logger.info("テキスト内に画像URLが見つかりませんでした。")
            return False

        prompt = "この画像が暴力的、もしくは性的な画像の場合trueと返してください。"
        try:
            response = self.openai_client.chat.completions.create(
                model=Settings().gpt_model,
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
            result = "true" in response.choices[0].message.content.lower()
            logger.info(f"画像検証結果: {'不適切' if result else '適切'}")
            return result
        except Exception as e:
            logger.error(f"画像検証中にエラーが発生しました: {e}")
            return True

    def judge_violation(self, text: str) -> bool:
        logger.info("コンテンツ違反の判定中...")
        response = self.openai_client.moderations.create(input=text)
        result = response.results[0].flagged or self.validate_image(text)
        logger.info(f"コンテンツ違反判定結果: {'違反あり' if result else '違反なし'}")
        return result

    @staticmethod
    def _extract_image_url(text: str) -> str:
        logger.info("テキストから画像URLを抽出中...")
        match = re.search(r"!\[[^\s]+\]\((https://[^\s]+)\)", text)
        url = match[1] if match and len(match) > 1 else ""
        logger.info(f"抽出された画像URL: {url}")
        return url

class QdrantHandler:
    def __init__(self, client: QdrantClient, openai_client: openai.Client):
        self.client = client
        self.openai_client = openai_client

    def add_issue(self, text: str, issue_number: int):
        logger.info(f"issue #{issue_number}をQdrantに追加中...")
        embedding = self._create_embedding(text)
        point = PointStruct(id=issue_number, vector=embedding, payload={"text": text})
        self.client.upsert(Settings().collection_name, [point])
        logger.success(f"issue #{issue_number}のQdrantへの追加が成功しました。")

    def search_similar_issues(self, text: str) -> List[Dict[str, Any]]:
        logger.info("類似issueの検索中...")
        embedding = self._create_embedding(text)
        results = self.client.search(collection_name=Settings().collection_name, query_vector=embedding)
        logger.info(f"{len(results)}件の類似issueが見つかりました。")
        return results[:Settings().max_results]

    def _create_embedding(self, text: str) -> List[float]:
        logger.info("テキストのembedding作成中...")
        result = self.openai_client.embeddings.create(input=[text], model=Settings().embedding_model)
        logger.success("embeddingの作成が成功しました。")
        return result.data[0].embedding

class IssueProcessor:
    def __init__(self, github_handler: GithubHandler, content_moderator: ContentModerator, qdrant_handler: QdrantHandler, openai_client: openai.Client):
        self.github_handler = github_handler
        self.content_moderator = content_moderator
        self.qdrant_handler = qdrant_handler
        self.openai_client = openai_client

    def process_issue(self, issue_content: str):
        logger.info("issueの処理を開始します...")
        if self.content_moderator.judge_violation(issue_content):
            logger.warning("issueの内容がガイドラインに違反しています。")
            self._handle_violation()
            return

        similar_issues = self.qdrant_handler.search_similar_issues(issue_content)
        if not similar_issues:
            logger.info("類似issueが見つかりませんでした。新しいissueをQdrantに追加します。")
            self.qdrant_handler.add_issue(issue_content, self.github_handler.issue.number)
            return

        duplicate_id = self._check_duplication(issue_content, similar_issues)
        if duplicate_id:
            logger.info(f"重複issueが見つかりました: #{duplicate_id}")
            self._handle_duplication(duplicate_id)
        else:
            logger.info("重複は見つかりませんでした。新しいissueをQdrantに追加します。")
            self.qdrant_handler.add_issue(issue_content, self.github_handler.issue.number)

    def _handle_violation(self):
        logger.info("コンテンツ違反の処理を開始します...")
        self.github_handler.add_label("toxic")
        self.github_handler.add_comment("不適切な投稿です。アカウントBANの危険性があります。")
        self.github_handler.close_issue()
        logger.success("違反の処理が完了しました。")

    def _check_duplication(self, issue_content: str, similar_issues: List[Dict[str, Any]]) -> int:
        logger.info("重複チェックを開始します...")
        prompt = self._create_duplication_check_prompt(issue_content, similar_issues)
        completion = self.openai_client.chat.completions.create(
            model=Settings().gpt_model,
            max_tokens=1024,
            messages=[{"role": "system", "content": prompt}]
        )
        review = completion.choices[0].message.content
        if ":" in review:
            review = review.split(":")[-1]
        result = int(review) if review.isdecimal() and review != "0" else 0
        logger.info(f"重複チェック結果: {result}")
        return result

    def _handle_duplication(self, duplicate_id: int):
        logger.info(f"issue #{duplicate_id}との重複を処理しています...")
        self.github_handler.add_label("duplicated")
        self.github_handler.add_comment(f"#{duplicate_id} と重複しているかもしれません")
        logger.success("重複の処理が完了しました。")

    @staticmethod
    def _create_duplication_check_prompt(issue_content: str, similar_issues: List[Dict[str, Any]]) -> str:
        logger.info("重複チェック用のプロンプトを作成しています...")
        similar_issues_text = "\n".join([f'id:{issue.id}\n内容:{issue.payload["text"]}' for issue in similar_issues])
        prompt = f"""
        以下は市民から寄せられた政策提案です。
        {issue_content}
        この投稿を読み、以下の過去提案の中に重複する提案があるかを判断してください。
        {similar_issues_text}
        重複する提案があればそのidを出力してください。
        もし存在しない場合は0と出力してください。

        [出力形式]
        id:0
        """
        logger.success("重複チェック用のプロンプトの作成が完了しました。")
        return prompt

def setup():
    logger.info("アプリケーションのセットアップを開始します...")
    config = Config()
    github_handler = GithubHandler(config)
    github_handler.create_labels()

    openai_client = openai.Client(api_key=config.settings.openai_api_key)  # OpenAI クライアントの初期化を修正
    content_moderator = ContentModerator(openai_client)

    qdrant_client = QdrantClient(url=config.settings.qd_url, api_key=config.settings.qd_api_key)
    qdrant_handler = QdrantHandler(qdrant_client, openai_client)

    logger.success("アプリケーションのセットアップが完了しました。")
    return github_handler, content_moderator, qdrant_handler, openai_client

def main():
    logger.info("メインプロセスを開始します...")
    try:
        github_handler, content_moderator, qdrant_handler, openai_client = setup()
        issue_processor = IssueProcessor(github_handler, content_moderator, qdrant_handler, openai_client)
        issue_content = f"{github_handler.issue.title}\n{github_handler.issue.body}"
        issue_processor.process_issue(issue_content)
        logger.success("メインプロセスが正常に完了しました。")
    except Exception as e:
        logger.error(f"メインプロセス中にエラーが発生しました: {e}")
        raise

if __name__ == "__main__":
    main()

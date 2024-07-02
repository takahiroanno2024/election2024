# デバッグガイド

このドキュメントでは、`.github/scripts/review_issue.py`スクリプトのデバッグ方法について説明します。

## 準備

1. リポジトリをローカルにクローンします：
   ```
   git clone https://github.com/yourusername/election2024.git
   cd election2024
   ```

2. `.github/scripts/`ディレクトリに移動します：
   ```
   cd .github/scripts/
   ```
   注意: 以降の全ての操作は、この`.github/scripts/`ディレクトリ内で行います。

3. `.env.example`ファイルを`.env`にコピーします：
   ```
   cp .env.example .env
   ```

4. `.env`ファイルを編集し、必要な環境変数を設定します：
   - `QD_API_KEY`: QdrantのAPIキー
   - `OPENAI_API_KEY`: OpenAIのAPIキー
   - `QD_URL`: QdrantのURL
   - `GITHUB_EVENT_ISSUE_NUMBER`: デバッグ用のIssue番号
   - `GITHUB_REPOSITORY`: あなたのGitHubリポジトリ（例：`username/repo`）
   - `GITHUB_TOKEN`: GitHubの個人アクセストークン

## デバッグの手順

1. 必要なライブラリをインストールします。`.github/scripts/`ディレクトリにいることを確認してから：
   ```
   pip install -r ../requirements.txt
   ```

2. `review_issue.py`のを実行しデバッグします。


## トラブルシューティング

- API接続エラーが発生した場合は、`.env`ファイルの認証情報が正しいか確認してください。
- GitHubのレート制限に遭遇した場合は、しばらく待ってから再試行してください。
- Qdrantの操作でエラーが発生した場合は、コレクションが正しく初期化されているか確認してください。

## テスト用Issue作成

デバッグ中に実際のIssueでテストしたい場合：

1. GitHubリポジトリ上で新しいIssueを作成します。
2. 作成したIssueの番号を`.env`ファイルの`GITHUB_EVENT_ISSUE_NUMBER`に設定します。
3. `.github/scripts/`ディレクトリにいることを確認し、スクリプトを実行します：
   ```
   python review_issue.py
   ```
4. Issueが正しく処理されるか確認します。

注意：テスト用のIssueを作成する際は、実際の運用環境に影響を与えないよう注意してください。

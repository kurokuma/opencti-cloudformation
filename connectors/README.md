# OpenCTI Connectors デプロイ

[OpenCTI公式コネクター](https://github.com/OpenCTI-Platform/connectors)を、既存の `opencti-core` スタックへECS Fargate Serviceとして追加するためのCloudFormationです。

## 設計方針

**テンプレートは1本（`opencti-connector.yaml`）のみ**で、全コネクターをパラメータで切り替えます。コネクターごとにテンプレートを複製すると、不具合修正のたびに全ファイルへ反映が必要になるためです。

コネクター固有の設定は2系統に分離しています。

| 設定の種類 | 置き場所 | 理由 |
|---|---|---|
| APIキー・トークン | **Secrets Manager** | S3のenvファイルに平文で置かない |
| それ以外の設定値 | **S3のenvファイル**（`env/*.env`） | 変更しやすく、タスク定義を汚さない |

## ファイル構成

```
connectors/
├── opencti-connector.yaml   汎用テンプレート（全コネクター共通）
├── README.md                本書
└── env/                     コネクター別の設定値（APIキーは含まない）
    ├── abuseipdb-ipblacklist.env
    ├── alienvault.env
    ├── crtsh.env
    ├── mitre.env
    ├── malwarebazaar.env
    ├── threatfox.env
    ├── recorded-future.env
    ├── ioc-extractor.env
    ├── shodan.env
    └── ipinfo.env
```

## 対象コネクター一覧

デプロイ時に指定する値です。`ConnectorScope` は公式のdocker-compose.ymlに準拠しています。

### external-import（外部フィードの定期取り込み）

| # | ConnectorName | Image | DisplayName | Scope | APIキー |
|---|---|---|---|---|---|
| 1 | `abuseipdb-ipblacklist` | `opencti/connector-abuseipdb-ipblacklist` | AbuseIPDB IP Blacklist | `abuseipdb` | `ABUSEIPDB_API_KEY` |
| 2 | `alienvault` | `opencti/connector-alienvault` | AlienVault | `alienvault` | `ALIENVAULT_API_KEY` |
| 3 | `crtsh` | `opencti/connector-crtsh` | crt.sh | `crtsh` | **不要** |
| 4 | `mitre` | `opencti/connector-mitre` | MITRE ATT&CK | `mitre` | **不要** |
| 5 | `malwarebazaar` | `opencti/connector-malwarebazaar` | MalwareBazaar | `StixFile` | `MALWAREBAZAAR_API_KEY` |
| 6 | `threatfox` | `opencti/connector-threatfox` | ThreatFox | `threatfox` | **不要** |
| 7 | `recorded-future` | `opencti/connector-recorded-future` | Recorded Future | `ipv4-addr,ipv6-addr,vulnerability,domain,url,StixFile` | `RECORDED_FUTURE_TOKEN` |

### internal-enrichment（OpenCTI内のエンティティを補強）

| # | ConnectorName | Image | DisplayName | Scope | APIキー |
|---|---|---|---|---|---|
| 8 | `ioc-extractor` | `opencti/connector-ioc-extractor` | IOC Extractor | `Report` | **不要** |
| 9 | `shodan` | `opencti/connector-shodan` | Shodan | `ipv4-addr,indicator` | `SHODAN_TOKEN` |
| 10 | `ipinfo` | `opencti/connector-ipinfo` | IpInfo | `IPv4-Addr,IPv6-Addr` | `IPINFO_TOKEN` |

> **APIキーの入手先**: AbuseIPDB / AlienVault OTX / abuse.ch（MalwareBazaar）/ Shodan / IPinfo は各サービスで無償アカウント登録により取得できます。**Recorded Future は商用契約が必要**です。

> **`crtsh` は追加設定が必須**です。`env/crtsh.env` の `CRTSH_DOMAIN` を監視対象ドメインへ変更してからアップロードしてください（既定値は `example.com`）。

## 個別デプロイについて

**コネクター1個 = 独立したCloudFormationスタック1個**です。必要なものだけを、任意の順序でデプロイできます。

- 一部だけ導入して、後から追加してもかまいません
- 1個だけ削除しても、他のコネクターとコアスタックには影響しません
- コネクター同士に依存関係はありません（依存はコアスタックへの一方向のみ）

リソース名がすべて `ConnectorName` で名前空間化されているため、衝突しません。

| リソース | 命名 |
|---|---|
| スタック | `opencti-connector-<NAME>` |
| ECSサービス / タスク定義ファミリー | `opencti-connector-<NAME>` |
| ロググループ | `/ecs/opencti/connectors/<NAME>` |
| Secrets Manager | `/opencti/connectors/<NAME>/token`、`/opencti/connectors/<NAME>/apikey` |
| IAMロール | CloudFormationが自動採番（名前指定なし） |

> **最初の1個は `mitre` を推奨します。** APIキーが不要で設定項目も少ないため、パイプライン全体の疎通確認に向いています。

## 事前確認

| 項目 | 内容 |
|---|---|
| コアスタック | `opencti-core` がデプロイ済みで稼働中 |
| 実行場所 | 以降のコマンドは **`connectors/` ディレクトリ内**で実行します |
| コスト影響 | コネクター1個につきFargateタスクが1個 常時稼働します（後述） |
| アウトバウンド | external-import系は外部サイトへHTTPS接続します。既存のNAT Gateway経由で到達可能です |

## 共通の準備

### 1. 変数とバケット名の取得

**PowerShell**

```powershell
$AWS_REGION = "us-west-1"
$CORE_STACK = "opencti-core"
$PROJECT    = "opencti"

$LIVE_BUCKET = aws cloudformation describe-stacks --region $AWS_REGION --stack-name $CORE_STACK --query 'Stacks[0].Outputs[?OutputKey==`LiveBucketName`].OutputValue' --output text
$LIVE_BUCKET
```

**bash**

```bash
export AWS_REGION=us-west-1
export CORE_STACK=opencti-core
export PROJECT=opencti

export LIVE_BUCKET=$(aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name "$CORE_STACK" --query 'Stacks[0].Outputs[?OutputKey==`LiveBucketName`].OutputValue' --output text)
echo "$LIVE_BUCKET"
```

> 変数はシェルのセッションを跨いで保持されません。ターミナルを開き直したら再設定してください。

### 2. OpenCTIでコネクター専用ユーザーを作る

コネクターごとに専用ユーザーを作り、必要最小限のGroup/Roleだけ付与してTokenを取得します。

```text
Settings → Security → Users → Add
```

> 検証目的で手早く進めたい場合は1つのユーザーを共用しても動作しますが、障害切り分けと権限分離の観点から**コネクターごとに分けることを推奨**します。

---

# 個別デプロイ サンプルコマンド

各コネクターのブロックは**それ単体で完結**しています。導入したいコネクターの節だけをコピーして実行してください。

共通する流れは次のとおりです。

1. 変数の設定（`CONNECTOR_ID` はここで新規生成）
2. OpenCTIのTokenをSecrets Managerへ登録
3. APIキーをSecrets Managerへ登録（必要なコネクターのみ）
4. envファイルをS3へアップロード
5. `aws cloudformation deploy` を実行

> **`$CONNECTOR_ID` は一度決めたら変更しないでください。** 変更するとOpenCTIから別コネクターとして認識され、既存のWork履歴やキューと紐づかなくなります。生成された値は必ず控えてください。

> PowerShellでは `ConvertTo-Json` + `file://`、bashでは `jq` を使ってJSONを生成しています。PowerShellが二重引用符を削除して壊れたJSONを保存する事故を防ぐためです。

---

## 1. abuseipdb-ipblacklist

AbuseIPDBのブラックリストIPを定期取得します。APIキーが必要です。

**PowerShell**

```powershell
$NAME="abuseipdb-ipblacklist"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"
$API_KEY="<AbuseIPDBのAPIキー>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text

@{ apiKey = $API_KEY } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$CRED_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/apikey" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-abuseipdb-ipblacklist:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=EXTERNAL_IMPORT" `
    "ConnectorDisplayName=AbuseIPDB IP Blacklist" `
    "ConnectorScope=abuseipdb" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" `
    "ConnectorCredential1EnvName=ABUSEIPDB_API_KEY" `
    "ConnectorCredential1SecretArn=$CRED_ARN"
```

**bash**

```bash
NAME=abuseipdb-ipblacklist
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'
API_KEY='<AbuseIPDBのAPIキー>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

CRED_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/apikey" \
  --secret-string "$(jq -n --arg k "$API_KEY" '{apiKey:$k}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-abuseipdb-ipblacklist:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=EXTERNAL_IMPORT" \
    "ConnectorDisplayName=AbuseIPDB IP Blacklist" \
    "ConnectorScope=abuseipdb" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" \
    "ConnectorCredential1EnvName=ABUSEIPDB_API_KEY" \
    "ConnectorCredential1SecretArn=$CRED_ARN"
```

---

## 2. alienvault

AlienVault OTXのPulseを取得します。APIキーが必要です。

初回は `env/alienvault.env` の `ALIENVAULT_PULSE_START_TIMESTAMP` 以降のPulseをすべて取得するため、日付を古くしすぎると初回同期が長時間になります。

**PowerShell**

```powershell
$NAME="alienvault"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"
$API_KEY="<AlienVault OTXのAPIキー>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text

@{ apiKey = $API_KEY } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$CRED_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/apikey" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-alienvault:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=EXTERNAL_IMPORT" `
    "ConnectorDisplayName=AlienVault" `
    "ConnectorScope=alienvault" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" `
    "ConnectorCredential1EnvName=ALIENVAULT_API_KEY" `
    "ConnectorCredential1SecretArn=$CRED_ARN"
```

**bash**

```bash
NAME=alienvault
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'
API_KEY='<AlienVault OTXのAPIキー>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

CRED_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/apikey" \
  --secret-string "$(jq -n --arg k "$API_KEY" '{apiKey:$k}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-alienvault:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=EXTERNAL_IMPORT" \
    "ConnectorDisplayName=AlienVault" \
    "ConnectorScope=alienvault" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" \
    "ConnectorCredential1EnvName=ALIENVAULT_API_KEY" \
    "ConnectorCredential1SecretArn=$CRED_ARN"
```

---

## 3. crtsh

証明書透明性ログ（crt.sh）から、指定ドメインの証明書を監視します。APIキーは不要です。

> **アップロード前に `env/crtsh.env` の `CRTSH_DOMAIN` を監視したいドメインへ変更してください。** 既定値 `example.com` のままでは意味のあるデータが取得できません。

**PowerShell**

```powershell
$NAME="crtsh"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-crtsh:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=EXTERNAL_IMPORT" `
    "ConnectorDisplayName=crt.sh" `
    "ConnectorScope=crtsh" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env"
```

**bash**

```bash
NAME=crtsh
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-crtsh:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=EXTERNAL_IMPORT" \
    "ConnectorDisplayName=crt.sh" \
    "ConnectorScope=crtsh" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env"
```

---

## 4. mitre

MITRE ATT&CKのナレッジを取り込みます。APIキーは不要で、**最初の疎通確認に最も適したコネクター**です。

取り込むデータ量が多いため、初回同期には時間がかかります。

**PowerShell**

```powershell
$NAME="mitre"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-mitre:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=EXTERNAL_IMPORT" `
    "ConnectorDisplayName=MITRE ATT&CK" `
    "ConnectorScope=mitre" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env"
```

**bash**

```bash
NAME=mitre
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-mitre:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=EXTERNAL_IMPORT" \
    "ConnectorDisplayName=MITRE ATT&CK" \
    "ConnectorScope=mitre" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env"
```

---

## 5. malwarebazaar

abuse.chのMalwareBazaarから最近のマルウェア検体情報を取得します。abuse.chのAPIキーが必要です。

**PowerShell**

```powershell
$NAME="malwarebazaar"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"
$API_KEY="<abuse.chのAPIキー>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text

@{ apiKey = $API_KEY } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$CRED_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/apikey" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-malwarebazaar:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=EXTERNAL_IMPORT" `
    "ConnectorDisplayName=MalwareBazaar" `
    "ConnectorScope=StixFile" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" `
    "ConnectorCredential1EnvName=MALWAREBAZAAR_API_KEY" `
    "ConnectorCredential1SecretArn=$CRED_ARN"
```

**bash**

```bash
NAME=malwarebazaar
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'
API_KEY='<abuse.chのAPIキー>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

CRED_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/apikey" \
  --secret-string "$(jq -n --arg k "$API_KEY" '{apiKey:$k}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-malwarebazaar:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=EXTERNAL_IMPORT" \
    "ConnectorDisplayName=MalwareBazaar" \
    "ConnectorScope=StixFile" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" \
    "ConnectorCredential1EnvName=MALWAREBAZAAR_API_KEY" \
    "ConnectorCredential1SecretArn=$CRED_ARN"
```

---

## 6. threatfox

abuse.chのThreatFoxからIOCを取得します。APIキーは不要です。

> **取り込み件数が非常に多い**コネクターです。OpenSearchのストレージ使用量（既定300GiB）の消費ペースを監視してください。

**PowerShell**

```powershell
$NAME="threatfox"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-threatfox:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=EXTERNAL_IMPORT" `
    "ConnectorDisplayName=ThreatFox" `
    "ConnectorScope=threatfox" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env"
```

**bash**

```bash
NAME=threatfox
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-threatfox:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=EXTERNAL_IMPORT" \
    "ConnectorDisplayName=ThreatFox" \
    "ConnectorScope=threatfox" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env"
```

---

## 7. recorded-future

**商用契約が必要**なコネクターです。`env/recorded-future.env` には利用プランに応じた設定項目が多数あるため、契約内容に合わせて見直してください。

**PowerShell**

```powershell
$NAME="recorded-future"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"
$API_KEY="<Recorded Futureのトークン>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text

@{ apiKey = $API_KEY } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$CRED_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/apikey" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-recorded-future:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=EXTERNAL_IMPORT" `
    "ConnectorDisplayName=Recorded Future" `
    "ConnectorScope=ipv4-addr,ipv6-addr,vulnerability,domain,url,StixFile" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" `
    "ConnectorCredential1EnvName=RECORDED_FUTURE_TOKEN" `
    "ConnectorCredential1SecretArn=$CRED_ARN"
```

**bash**

```bash
NAME=recorded-future
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'
API_KEY='<Recorded Futureのトークン>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

CRED_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/apikey" \
  --secret-string "$(jq -n --arg k "$API_KEY" '{apiKey:$k}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-recorded-future:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=EXTERNAL_IMPORT" \
    "ConnectorDisplayName=Recorded Future" \
    "ConnectorScope=ipv4-addr,ipv6-addr,vulnerability,domain,url,StixFile" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" \
    "ConnectorCredential1EnvName=RECORDED_FUTURE_TOKEN" \
    "ConnectorCredential1SecretArn=$CRED_ARN"
```

---

## 8. ioc-extractor

Report本文からIOCを抽出します。APIキーは不要です。

既定は `CONNECTOR_AUTO=false`（手動実行）です。全Reportで自動実行したい場合は `env/ioc-extractor.env` を `true` に変更してください。

> 負荷が軽いコネクターのため、タスクサイズを最小（`ConnectorCpu=256` / `ConnectorMemory=512`）にしています。

**PowerShell**

```powershell
$NAME="ioc-extractor"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-ioc-extractor:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=INTERNAL_ENRICHMENT" `
    "ConnectorDisplayName=IOC Extractor" `
    "ConnectorScope=Report" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" `
    "ConnectorCpu=256" `
    "ConnectorMemory=512"
```

**bash**

```bash
NAME=ioc-extractor
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-ioc-extractor:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=INTERNAL_ENRICHMENT" \
    "ConnectorDisplayName=IOC Extractor" \
    "ConnectorScope=Report" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" \
    "ConnectorCpu=256" \
    "ConnectorMemory=512"
```

---

## 9. shodan

IPアドレスやIndicatorをShodanの情報で補強します。Shodanのトークンが必要です。

> 既定で `CONNECTOR_AUTO=true` のため、**スコープ内のObservableが作られるたびにAPIクレジットを消費**します。無償プランはクレジット上限が低いため、消費が早い場合は `env/shodan.env` で `CONNECTOR_AUTO=false` にしてください。

**PowerShell**

```powershell
$NAME="shodan"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"
$API_KEY="<Shodanのトークン>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text

@{ apiKey = $API_KEY } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$CRED_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/apikey" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-shodan:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=INTERNAL_ENRICHMENT" `
    "ConnectorDisplayName=Shodan" `
    "ConnectorScope=ipv4-addr,indicator" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" `
    "ConnectorCredential1EnvName=SHODAN_TOKEN" `
    "ConnectorCredential1SecretArn=$CRED_ARN" `
    "ConnectorCpu=256" `
    "ConnectorMemory=512"
```

**bash**

```bash
NAME=shodan
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'
API_KEY='<Shodanのトークン>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

CRED_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/apikey" \
  --secret-string "$(jq -n --arg k "$API_KEY" '{apiKey:$k}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-shodan:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=INTERNAL_ENRICHMENT" \
    "ConnectorDisplayName=Shodan" \
    "ConnectorScope=ipv4-addr,indicator" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" \
    "ConnectorCredential1EnvName=SHODAN_TOKEN" \
    "ConnectorCredential1SecretArn=$CRED_ARN" \
    "ConnectorCpu=256" \
    "ConnectorMemory=512"
```

---

## 10. ipinfo

IPアドレスを地理情報・ASN情報で補強します。IPinfoのトークンが必要です。

> こちらも既定で `CONNECTOR_AUTO=true` です。APIの消費量に注意してください。

**PowerShell**

```powershell
$NAME="ipinfo"; $CONNECTOR_ID=[guid]::NewGuid().ToString(); $CONNECTOR_ID
$OPENCTI_TOKEN="<OpenCTIで発行したToken>"
$API_KEY="<IPinfoのトークン>"

@{ token = $OPENCTI_TOKEN } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$TOKEN_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/token" --secret-string file://_s.json --query ARN --output text

@{ apiKey = $API_KEY } | ConvertTo-Json -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$PWD\_s.json", $_) }
$CRED_ARN = aws secretsmanager create-secret --region $AWS_REGION --name "/opencti/connectors/$NAME/apikey" --secret-string file://_s.json --query ARN --output text
Remove-Item _s.json

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region $AWS_REGION `
  --stack-name "opencti-connector-$NAME" `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    "ConnectorName=$NAME" `
    "ConnectorImage=opencti/connector-ipinfo:latest" `
    "ConnectorId=$CONNECTOR_ID" `
    "ConnectorType=INTERNAL_ENRICHMENT" `
    "ConnectorDisplayName=IpInfo" `
    "ConnectorScope=IPv4-Addr,IPv6-Addr" `
    "ConnectorTokenSecretArn=$TOKEN_ARN" `
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" `
    "ConnectorCredential1EnvName=IPINFO_TOKEN" `
    "ConnectorCredential1SecretArn=$CRED_ARN" `
    "ConnectorCpu=256" `
    "ConnectorMemory=512"
```

**bash**

```bash
NAME=ipinfo
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "$CONNECTOR_ID"
OPENCTI_TOKEN='<OpenCTIで発行したToken>'
API_KEY='<IPinfoのトークン>'

TOKEN_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/token" \
  --secret-string "$(jq -n --arg t "$OPENCTI_TOKEN" '{token:$t}')" \
  --query ARN --output text)

CRED_ARN=$(aws secretsmanager create-secret --region "$AWS_REGION" \
  --name "/opencti/connectors/$NAME/apikey" \
  --secret-string "$(jq -n --arg k "$API_KEY" '{apiKey:$k}')" \
  --query ARN --output text)

aws s3 cp "env/$NAME.env" "s3://$LIVE_BUCKET/connector-env/$NAME.env"

aws cloudformation deploy --region "$AWS_REGION" \
  --stack-name "opencti-connector-$NAME" \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "CoreStackName=$CORE_STACK" \
    "ConnectorName=$NAME" \
    "ConnectorImage=opencti/connector-ipinfo:latest" \
    "ConnectorId=$CONNECTOR_ID" \
    "ConnectorType=INTERNAL_ENRICHMENT" \
    "ConnectorDisplayName=IpInfo" \
    "ConnectorScope=IPv4-Addr,IPv6-Addr" \
    "ConnectorTokenSecretArn=$TOKEN_ARN" \
    "ConnectorEnvironmentFileArn=arn:aws:s3:::$LIVE_BUCKET/connector-env/$NAME.env" \
    "ConnectorCredential1EnvName=IPINFO_TOKEN" \
    "ConnectorCredential1SecretArn=$CRED_ARN" \
    "ConnectorCpu=256" \
    "ConnectorMemory=512"
```

---

# 運用

## 動作確認

**PowerShell**

```powershell
aws logs tail "/ecs/opencti/connectors/$NAME" --region $AWS_REGION --since 15m --follow
```

**bash**

```bash
aws logs tail "/ecs/opencti/connectors/$NAME" --region "$AWS_REGION" --since 15m --follow
```

OpenCTIのUIで登録状況を確認します。

```text
Data → Ingestion → Connectors
```

`Active` 表示になり、external-import系は `Messages` が増えていけば正常です。

## コスト影響

**コネクター1個 = ECSサービス1個 = 常時稼働するFargateタスク1個**です。スケジュール実行のコネクターも、コンテナ内部で実行間隔を管理するため常駐します。

| タスクサイズ | 1個あたり月額 | 10個すべて |
|---|---:|---:|
| 0.5 vCPU / 1 GiB（既定） | 約 ¥3,400 | 約 ¥34,000 |
| 0.25 vCPU / 0.5 GiB（最小） | 約 ¥1,700 | 約 ¥17,000 |

※Fargate料金のみ。Secrets Manager（$0.40/シークレット）とログ・NAT通信が少額加算されます。

本書のサンプルでは、負荷の軽い internal-enrichment 系（`ioc-extractor` / `shodan` / `ipinfo`）を最小サイズで指定しています。external-import 系、特に `mitre` は初回同期のデータ量が大きいため既定サイズのままにしてください。

加えて、external-import系は取り込み量に応じて**OpenSearchのストレージ**とNATのデータ処理量が増えます。特に `threatfox` / `alienvault` / `mitre` はデータ量が多いため、既定の300GiBの消費ペースを監視してください。

### 一時停止

スタックを削除せずタスク数を0にできます。ログ・設定・Secretは保持され、再開は `--desired-count 1` に戻すだけです。

```powershell
aws ecs update-service --region $AWS_REGION --cluster "${PROJECT}-cluster" --service "opencti-connector-$NAME" --desired-count 0
```

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `ECS Deployment Circuit Breaker was triggered` | タスクが安定稼働に至らなかった（結果のみで原因は含まない） | まずログを確認。**ログが空ならコンテナ起動前の失敗**（Secret取得・envファイル取得・イメージPull） |
| ログが空でタスクが落ちる | Secretが壊れている | 下記のコマンドでJSONとして読めるか確認 |
| `Connector not found` | `ConnectorId` の誤り | **管理者トークンと取り違えていないか確認**（どちらもUUID形式のため紛らわしい） |
| `ResourceInitializationError: unable to pull env file` | envファイルのARN誤り、または未アップロード | S3にオブジェクトが存在するか確認 |
| 認証エラー（401/403） | APIキーが誤っている、または期限切れ | Secretの値と、`ConnectorCredential1EnvName` が公式の変数名と一致しているか確認 |
| `CannotPullContainerError` | Docker Hubのレート制限 | 時間を空けるか、イメージをECRへミラー |
| envファイルの値が効かない | 変更後にタスクが再起動していない | envファイルは**タスク起動時のみ**読まれる。下記で強制再デプロイ |
| changeset作成が `ResourceExistenceCheck` で失敗 | 前回のロググループが残っている | 「撤去」の手順でロググループを削除してから再デプロイ |

Secretの中身を検証します。

```powershell
aws secretsmanager get-secret-value --region $AWS_REGION --secret-id "/opencti/connectors/$NAME/token" --query SecretString --output text | ConvertFrom-Json
```

```bash
aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "/opencti/connectors/$NAME/token" --query SecretString --output text | jq
```

envファイル変更後の反映方法です。

```powershell
aws ecs update-service --region $AWS_REGION --cluster "${PROJECT}-cluster" --service "opencti-connector-$NAME" --force-new-deployment
```

## 撤去

```powershell
aws cloudformation delete-stack --region $AWS_REGION --stack-name "opencti-connector-$NAME"
aws cloudformation wait stack-delete-complete --region $AWS_REGION --stack-name "opencti-connector-$NAME"
```

CloudWatch Logsは `DeletionPolicy: Retain` のため残ります。**同名で再デプロイする場合は先に削除しないと、早期検証（ResourceExistenceCheck）でchangeset作成が失敗します。**

```powershell
aws logs delete-log-group --region $AWS_REGION --log-group-name "/ecs/opencti/connectors/$NAME"
```

Secrets Managerに登録したTokenとAPIキーも手動削除が必要です。

```powershell
aws secretsmanager delete-secret --region $AWS_REGION --secret-id "/opencti/connectors/$NAME/token" --force-delete-without-recovery
aws secretsmanager delete-secret --region $AWS_REGION --secret-id "/opencti/connectors/$NAME/apikey" --force-delete-without-recovery
```

## 参考

- OpenCTI Connectors（公式リポジトリ）: https://github.com/OpenCTI-Platform/connectors
- external-import 一覧: https://github.com/OpenCTI-Platform/connectors/tree/master/external-import
- internal-enrichment 一覧: https://github.com/OpenCTI-Platform/connectors/tree/master/internal-enrichment
- OpenCTI Connectors ドキュメント: https://docs.opencti.io/latest/deployment/connectors/

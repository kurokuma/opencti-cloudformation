# OpenCTI on AWS CloudFormation

QinetiQ Cyber Intelligenceの[OpenCTI-Terraform](https://github.com/QinetiQ-Cyber-Intelligence/OpenCTI-Terraform)を参考に、次の要件へ合わせてCloudFormationとして再設計したもの

- OpenCTI、Worker、Connector、SSM中継EC2はPublic IPを持たない
- OpenCTIへの管理アクセスはSession Managerのリモートホスト・ポートフォワーディングのみ
- 外向き通信は単一NAT Gateway経由
- マルチAZ、ALB、水平スケールは使用しない
- OpenCTI PlatformとWorkerはECS Fargate
- MinIOはAmazon S3
- ElasticsearchはAmazon OpenSearch Service
- RedisはAmazon ElastiCache
- RabbitMQはAmazon MQ for RabbitMQ
- ECSタスクのIP変更はAWS Cloud MapのPrivate DNSで吸収
- Connectorはコアと別スタックで追加

## ファイル

| ファイル | 用途 |
|---|---|
| `opencti-core.yaml` | VPC、NAT、SSM中継、ECS、OpenSearch、Redis、RabbitMQ、S3、Platform、Worker |
| `opencti-connector.yaml` | Connectorを1個ずつECS Fargate Serviceとして追加 |
| `example-import-file-stix.env` | ImportFileStix Connector用の環境変数例 |
| `exmaple/example-stix-bundle.json` | 取り込み動作を検証するための最小STIX 2.1バンドル |
| `parameters.example.json` | コアスタックのパラメータ雛形（参照用・コミット対象） |

## Terraform版から変更した点

参照リポジトリは、3AZ、公開／内部ロードバランサー、ECS Fargate上のRabbitMQ＋EFS、OpenSearchの内部ユーザー認証を採用しています。本テンプレートでは以下へ変更

| Terraform版 | 本テンプレート |
|---|---|
| 3AZ | 1AZ |
| Public ALB＋Private NLB | ALB/NLBなし、Cloud Map Private DNS |
| OpenCTI Platformを公開可能 | 完全Private、SSMポートフォワーディングのみ |
| RabbitMQ on ECS＋EFS | Amazon MQ Single Instance |
| OpenSearchユーザー／パスワード | ECS Task RoleによるSigV4 IAM認証 |
| ConnectorをTerraform Moduleで追加 | Connector用CloudFormationスタックを1個ずつ追加 |
| ARM64 Platform、x86 Connector | Platform、Worker、Connectorをx86_64で統一 |

NAT Gatewayだけはインターネット接続のためElastic IPを持ちEC2、ECSタスク、OpenSearch、Redis、RabbitMQにはPublic IPを付与しない

---

# デプロイ手順

以下は`us-west-1`を例にした手順だが、個人の環境に合わせて変更すること

bash（Linux / macOS）:

```bash
export AWS_REGION=us-west-1
export CORE_STACK=opencti-core
export PROJECT=opencti
```

PowerShell（Windows）:

```powershell
$AWS_REGION = "us-west-1"
$CORE_STACK = "opencti-core"
$PROJECT    = "opencti"
```

## 0. 前提ツールと権限

### 0.1 ローカル端末に必要なツール

| ツール | 用途 | 確認コマンド |
|---|---|---|
| AWS CLI v2 | スタック操作 | `aws --version` |
| Session Manager Plugin | SSMポートフォワーディング | `session-manager-plugin --version` |
| jq | Secret/JSONの取り出し | `jq --version` |
| python3 | UUID生成 | `python3 --version` |

Session Manager Pluginが未導入の場合は、AWS公式手順でインストール
<https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html>

### 0.2 認証情報とIAM権限

```bash
# 認証情報とリージョンが正しいか確認
aws sts get-caller-identity
aws configure get region
```

CloudFormationを実行するIAM Principalには、少なくとも次のリソースの作成権限が必要

- VPC、Subnet、Route Table、Internet Gateway、NAT Gateway、EIP、VPC Endpoint
- ECS、EC2、IAM（Role/InstanceProfile作成）、CloudWatch Logs
- OpenSearch Service
- ElastiCache（ReplicationGroup、SubnetGroup、ParameterGroup）
- Amazon MQ
- S3、Secrets Manager、Cloud Map（ServiceDiscovery）

IAM Roleを作成するため、`deploy`時に`--capabilities CAPABILITY_IAM`が必須

### 0.3 サービスクォータ（初回アカウントで不足しがちな項目）

- **NAT Gateway / Elastic IP**：リージョンで最低1つずつ空きが必要
- **Fargate vCPU（オンデマンド）**：Platform 2 vCPU＋Worker 1 vCPU×2＝計4 vCPU以上
- **VPC数**：新規VPCを1つ作成

### 0.4 OpenSearchのサービスリンクロール（アカウントで初回のみ・必須）

**VPC内にOpenSearchドメインを作成するには、アカウントに`AWSServiceRoleForAmazonOpenSearchService`が存在している必要がある
** 未作成のままデプロイすると、OpenSearchドメイン作成時に次のエラーでスタックがロールバックする

```text
Before you can proceed, you must enable a service-linked role to give
Amazon OpenSearch Service permissions to access your VPC.
```

このロールはドメイン初回作成時に自動生成される仕様だが、**その「初回」の作成自体は上記エラーで失敗することがある**（SLRの生成とドメイン作成が同一リクエスト内で競合するため）。つまり1回目は失敗し、SLRだけが残る、という挙動になり得ます。事故を避けるため、**デプロイ前に明示的に作成しておくのが確実**

まず存在と作成日時を確認する（IAMはグローバルなためリージョン指定不要。アカウントに1つあれば全リージョンで有効）

```powershell
aws iam get-role --role-name AWSServiceRoleForAmazonOpenSearchService --query 'Role.{Name:RoleName,Created:CreateDate}' --output table
```

`NoSuchEntity`エラーになる場合は作成する

```powershell
aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com
```

すでに存在する場合は`has been taken in this account`が返る。エラー表示だが**問題なし**（作成済みという意味）。


## 1. リージョン可用性の事前確認

指定するインスタンスタイプとエンジンバージョンが、対象リージョンで利用可能か確認します。ここで返らない値をパラメータに渡すとCREATEが失敗

```bash
# OpenSearchの利用可能バージョン（既定: OpenSearch_2.17）
aws opensearch list-versions --region "$AWS_REGION"
```

応答サンプル

```bash
aws opensearch list-versions --region "us-west-1"
{
    "Versions": [
        "OpenSearch_3.5",
        "OpenSearch_3.3",
        "OpenSearch_3.1",
        "OpenSearch_2.19",
        "OpenSearch_2.17",
        "OpenSearch_2.15",
        "OpenSearch_2.13",
        "OpenSearch_2.11",
        "OpenSearch_2.9",
        "OpenSearch_2.7",
        "OpenSearch_2.5",
        "OpenSearch_2.3",
        "OpenSearch_1.3",
        "OpenSearch_1.2",
        "OpenSearch_1.1",
        "OpenSearch_1.0",
        "Elasticsearch_7.10",
        "Elasticsearch_7.9",
        "Elasticsearch_7.8",
        "Elasticsearch_7.7",
        "Elasticsearch_7.4",
        "Elasticsearch_7.1",
        "Elasticsearch_6.8",
        "Elasticsearch_6.7",
        "Elasticsearch_6.5",
        "Elasticsearch_6.4",
        "Elasticsearch_6.3",
        "Elasticsearch_6.2",
        "Elasticsearch_6.0",
        "Elasticsearch_5.6",
        "Elasticsearch_5.5",
        "Elasticsearch_5.3",
        "Elasticsearch_5.1",
        "Elasticsearch_2.3",
        "Elasticsearch_1.5"
    ]
}
```

# Amazon MQ RabbitMQのインスタンス／バージョン（既定: mq.m7g.medium / 3.13）

```bash
aws mq describe-broker-instance-options \
  --engine-type RABBITMQ \
  --host-instance-type mq.m7g.medium \
  --region "$AWS_REGION"
```

PowerShell:

```powershell
aws mq describe-broker-instance-options `
  --engine-type RABBITMQ `
  --host-instance-type mq.m7g.medium `
  --region $AWS_REGION
```

応答サンプル

```bash
aws mq describe-broker-instance-options --engine-type RABBITMQ --host-instance-type mq.m7g.medium --region "us-west-1"
{
    "BrokerInstanceOptions": [
        {
            "AvailabilityZones": [
                {
                    "Name": "us-west-1c"
                },
                {
                    "Name": "us-west-1a"
                }
            ],
            "EngineType": "RABBITMQ",
            "HostInstanceType": "mq.m7g.medium",
            "StorageType": "ebs",
            "SupportedDeploymentModes": [
                "SINGLE_INSTANCE",
                "CLUSTER_MULTI_AZ"
            ],
            "SupportedEngineVersions": [
                "4.2",
                "3.13"
            ]
        }
    ],
    "MaxResults": 20
}
```

### OpenSearchインスタンスタイプの可用性

`r7g`系（Graviton3）は**古いリージョンでは提供されていない**ことがあります。バージョンだけでなく**インスタンスタイプ**も必ず確認

```powershell
aws opensearch list-instance-type-details --engine-version OpenSearch_2.17 --region $AWS_REGION --query 'InstanceTypeDetails[].InstanceType' --output text
```

`r7g.large.search`が一覧に無ければ、`OpenSearchInstanceType`を利用可能なタイプ（例：`r6g.large.search`、`m6g.large.search`）へ変更

`cache.r7g.large`（Redis）も同様に確認
ElastiCacheには「利用可能ノードタイプ一覧」APIが無いため、予約ノードのオファリング有無を代替指標として使う

```powershell
aws elasticache describe-reserved-cache-nodes-offerings --cache-node-type cache.r7g.large --region $AWS_REGION --query 'length(ReservedCacheNodesOfferings)' --output text
```

`0`が返る、またはエラーになる場合は、そのリージョンで未提供の可能性が高いため`RedisNodeType`を`cache.r6g.large`などへ変更

## 2. OpenCTI管理者Tokenを生成

OpenCTIの`APP__ADMIN__TOKEN`はUUIDv4で作成

bash:

```bash
export OPENCTI_ADMIN_TOKEN=$(python3 -c 'import uuid; print(uuid.uuid4())')
echo "$OPENCTI_ADMIN_TOKEN"
```

PowerShell（.NETでUUIDv4を生成）:

```powershell
$OPENCTI_ADMIN_TOKEN = [guid]::NewGuid().ToString()
$OPENCTI_ADMIN_TOKEN
```

このTokenはWorkerとの認証使用
次の手順で`parameters.json`へ埋め込み

## 3. コアスタックをデプロイ

パラメータは`parameters.json`（`parameters.example.json`と同じCloudFormation形式）にまとめ、`--parameter-overrides file://parameters.json`で渡す

### 3.1 parameters.json を用意

雛形`parameters.example.json`をコピーし、手順2で生成したTokenを埋め込み
`OpenCTIAdminEmail`など他の値は必要に応じて変更

bash:

```bash
# 雛形からコピーしつつ Token を埋め込む
sed "s/REPLACE-WITH-UUID-V4/${OPENCTI_ADMIN_TOKEN}/" parameters.example.json > parameters.json
cat parameters.json
```

PowerShell（BOM無しUTF-8で保存。`Set-Content -Encoding utf8`はPS5.1でBOMを付与しAWS CLIのJSON解析に失敗し得るため`WriteAllText`を使用）:

```powershell
$json = (Get-Content parameters.example.json -Raw) -replace 'REPLACE-WITH-UUID-V4', $OPENCTI_ADMIN_TOKEN
[System.IO.File]::WriteAllText("$PWD\parameters.json", $json)
Get-Content parameters.json
```

`parameters.json`にはTokenが入る

### 3.2 デプロイ

bash:

```bash
aws cloudformation deploy \
  --region "$AWS_REGION" \
  --stack-name "$CORE_STACK" \
  --template-file opencti-core.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides file://parameters.json
```

PowerShell:

```powershell
aws cloudformation deploy `
  --region $AWS_REGION `
  --stack-name $CORE_STACK `
  --template-file opencti-core.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides file://parameters.json
```

`deploy`が失敗した場合は自動でロールバックされ、失敗理由は次で特定

bash:

```bash
aws cloudformation describe-stack-events \
  --region "$AWS_REGION" \
  --stack-name "$CORE_STACK" \
  --query 'StackEvents[?contains(ResourceStatus, `FAILED`)].{Type:ResourceType,Reason:ResourceStatusReason}' \
  --output table
```

powershell:

```powershell
aws cloudformation describe-stack-events `
  --region $AWS_REGION `
  --stack-name $CORE_STACK `
  --query 'StackEvents[?contains(ResourceStatus, `FAILED`)].{Type:ResourceType,Reason:ResourceStatusReason}' `
  --output table
```

## 4. デプロイ状態の確認と切り分け

CREATE_COMPLETE後、OpenCTI Platformが実際に起動したかを確認します。

### 4.1 スタック状態と出力値

bash:

```bash
aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$CORE_STACK" \
  --query 'Stacks[0].StackStatus' \
  --output text

# 出力値（Instance ID、Secret ARN、Bucket名など）を一覧
aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$CORE_STACK" \
  --query 'Stacks[0].Outputs' \
  --output table
```

PowerShell:

```powershell
aws cloudformation describe-stacks `
  --region $AWS_REGION `
  --stack-name $CORE_STACK `
  --query 'Stacks[0].StackStatus' `
  --output text

# 出力値（Instance ID、Secret ARN、Bucket名など）を一覧
aws cloudformation describe-stacks `
  --region $AWS_REGION `
  --stack-name $CORE_STACK `
  --query 'Stacks[0].Outputs' `
  --output table
```

### 4.2 ECSサービスの安定状態

Platform（DesiredCount=1）とWorker（既定DesiredCount=2）がRunningになっているか確認

bash:

```bash
aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "${PROJECT}-cluster" \
  --services "${PROJECT}-platform" "${PROJECT}-worker" \
  --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount,rollout:deployments[0].rolloutState}' \
  --output table
```

PowerShell:

```powershell
aws ecs describe-services `
  --region $AWS_REGION `
  --cluster "${PROJECT}-cluster" `
  --services "${PROJECT}-platform" "${PROJECT}-worker" `
  --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount,rollout:deployments[0].rolloutState}' `
  --output table
```

`running`が`desired`と一致し、`rollout`が`COMPLETED`なら正常

### 4.3 起動ログの確認

```bash
# Platformの起動ログをリアルタイム表示
aws logs tail "/ecs/${PROJECT}/platform" --region "$AWS_REGION" --since 15m --follow

# Workerのログ
aws logs tail "/ecs/${PROJECT}/worker" --region "$AWS_REGION" --since 15m --follow
```

正常時はPlatformログに OpenSearch / Redis / RabbitMQ / S3 への接続成功と、`Platform initialization done` 系のメッセージが出る

### 4.4 うまくいかないときの主な原因

| 症状 | 主な原因と対処 |
|---|---|
| Platformタスクが起動→停止を繰り返す | RabbitMQ管理API（443）、OpenSearch、Redisいずれかへの接続失敗。4.3のログでどの依存かを特定 |
| `CannotPullContainerError` | Docker Hubの匿名Pullレート制限。時間を置くか、イメージをECRへミラーして`OpenCTIPlatformImage`に指定 |
| OpenSearchで403 | Task RoleのSigV4認証。`OPENSEARCH__REGION`と`OpenSearchDomain`のAccessPoliciesが一致しているか確認（既定は整合済み） |
| Circuit breakerでロールバック | Platformが安定状態に到達できず。4.1のFAILEDイベントと4.3のログを併読 |
| OpenSearchで`Throughput must be between 250 and 1250` | gp3スループットの下限はOpenSearchでは**250**（EC2 EBSの125とは異なる）。`OpenSearchVolumeThroughput`は既定250で修正済み |
| OpenSearchで`you must enable a service-linked role` | `AWSServiceRoleForAmazonOpenSearchService`が未作成。**0.4**の手順で作成してから再デプロイ |
| OpenSearchで`InvalidTypeException`／インスタンスタイプ関連 | 対象リージョンで`r7g`系が未提供。**1章**の`list-instance-type-details`で確認し`OpenSearchInstanceType`を変更 |
| `AWS::AmazonMQ::Broker ... did not stabilize` | 多くは**他リソースの失敗によるロールバックの巻き添え**（作成中のBrokerが削除され安定化できない）。同一イベント内に他のFAILEDが無い場合のみMQ固有の問題を疑う |
| `[AWS::EarlyValidation::ResourceExistenceCheck]` でchangeset作成が失敗 | **既存リソースとの名前衝突**。前回失敗時の残存リソースが原因。4.5でクリーンアップする |

## 5. 初期パスワードを取得

管理者の初期パスワードはSecrets Managerに自動生成される

bash:

```bash
ADMIN_SECRET_ARN=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$CORE_STACK" \
  --query 'Stacks[0].Outputs[?OutputKey==`OpenCTIAdminSecretArn`].OutputValue' \
  --output text)

aws secretsmanager get-secret-value \
  --region "$AWS_REGION" \
  --secret-id "$ADMIN_SECRET_ARN" \
  --query SecretString \
  --output text | jq
```

PowerShell:

```powershell
$ADMIN_SECRET_ARN = aws cloudformation describe-stacks `
  --region $AWS_REGION `
  --stack-name $CORE_STACK `
  --query 'Stacks[0].Outputs[?OutputKey==`OpenCTIAdminSecretArn`].OutputValue' `
  --output text

aws secretsmanager get-secret-value `
  --region $AWS_REGION `
  --secret-id $ADMIN_SECRET_ARN `
  --query SecretString `
  --output text | ConvertFrom-Json
```

Secretには次のキーがある

```json
{
  "email": "admin@example.com",
  "password": "generated-password",
  "token": "uuid-v4"
}
```

## 6. SSMポートフォワーディングでアクセス

Platformは完全Privateであるため、SSM中継EC2経由でローカルの`4000`番へトンネルする

bash:

```bash
RELAY_INSTANCE_ID=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$CORE_STACK" \
  --query 'Stacks[0].Outputs[?OutputKey==`SSMRelayInstanceId`].OutputValue' \
  --output text)

aws ssm start-session \
  --region "$AWS_REGION" \
  --target "$RELAY_INSTANCE_ID" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["platform.opencti.local"],"portNumber":["4000"],"localPortNumber":["4000"]}'
```

PowerShell:

```powershell
$RELAY_INSTANCE_ID = aws cloudformation describe-stacks `
  --region $AWS_REGION `
  --stack-name $CORE_STACK `
  --query 'Stacks[0].Outputs[?OutputKey==`SSMRelayInstanceId`].OutputValue' `
  --output text

aws ssm start-session `
  --region $AWS_REGION `
  --target $RELAY_INSTANCE_ID `
  --document-name AWS-StartPortForwardingSessionToRemoteHost `
  --parameters '{\"host\":[\"platform.opencti.local\"],\"portNumber\":[\"4000\"],\"localPortNumber\":[\"4000\"]}'
```

> **PowerShellでのJSON引数の注意**：PowerShellはネイティブコマンドへ引数を渡す際、**単一引用符内であっても二重引用符を削除**。bashと同じ`'{"host":...}'`と書くと、AWS CLIには`{host:...}`という不正なJSONが届き、次のエラーになる
>
> ```text
> Error parsing parameter '--parameters': Invalid JSON: Expecting property name enclosed in double quotes
> ```
>
> 上記のように二重引用符を`\"`とバックスラッシュでエスケープ。引用符が多く読みにくい場合は、JSONをファイルに書き出して`file://`で渡す方法も使える
>
> ```powershell
> '{"host":["platform.opencti.local"],"portNumber":["4000"],"localPortNumber":["4000"]}' | Set-Content portforward.json -Encoding ascii
> aws ssm start-session --region $AWS_REGION --target $RELAY_INSTANCE_ID --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters file://portforward.json
> ```

このセッションを**開いたまま**、別ウィンドウのブラウザで次へアクセス

```text
http://localhost:4000
```

手順5の`email`／`password`でログイン


## 7. Connector追加の基本フロー

参照Terraformと同様、Connectorはコアから分離。ConnectorごとにOpenCTI上の専用ユーザーとTokenを作る

### 7.1 OpenCTIで専用ユーザーを作る

例：ImportFileStix

1. OpenCTIの`Settings`→`Security`→`Users`で専用ユーザーを作成
2. 必要なGroup／Roleだけを付与
3. User Tokenを取得

`INTERNAL_EXPORT_FILE` Connectorは、要求ユーザーを代理してデータを出力するため、権限設定を特に確認

### 7.2 TokenをSecrets Managerへ登録

bash:

```bash
aws secretsmanager create-secret \
  --region "$AWS_REGION" \
  --name /opencti/connectors/import-file-stix/token \
  --secret-string '{"token":"OPENCTI_CONNECTOR_USER_TOKEN"}'

CONNECTOR_TOKEN_SECRET_ARN=$(aws secretsmanager describe-secret \
  --region "$AWS_REGION" \
  --secret-id /opencti/connectors/import-file-stix/token \
  --query ARN \
  --output text)
```

PowerShell:

```powershell
# OpenCTIで発行した実際のTokenに置き換える
$CONNECTOR_TOKEN = "OPENCTI_CONNECTOR_USER_TOKEN"

# ConvertTo-Jsonで確実に正しいJSONを生成し、ファイル経由で渡す（引用符問題を完全回避）
@{ token = $CONNECTOR_TOKEN } | ConvertTo-Json -Compress |
  ForEach-Object { [System.IO.File]::WriteAllText("$PWD\connector-token.json", $_) }

aws secretsmanager create-secret `
  --region $AWS_REGION `
  --name /opencti/connectors/import-file-stix/token `
  --secret-string file://connector-token.json

Remove-Item connector-token.json

$CONNECTOR_TOKEN_SECRET_ARN = aws secretsmanager describe-secret `
  --region $AWS_REGION `
  --secret-id /opencti/connectors/import-file-stix/token `
  --query ARN `
  --output text
```

### 7.3 Connector固有の環境変数をS3へ置く

bash:

```bash
LIVE_BUCKET=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$CORE_STACK" \
  --query 'Stacks[0].Outputs[?OutputKey==`LiveBucketName`].OutputValue' \
  --output text)

aws s3 cp \
  example-import-file-stix.env \
  "s3://${LIVE_BUCKET}/connector-env/import-file-stix.env"

ENV_FILE_ARN="arn:aws:s3:::${LIVE_BUCKET}/connector-env/import-file-stix.env"
```

PowerShell:

```powershell
$LIVE_BUCKET = aws cloudformation describe-stacks `
  --region $AWS_REGION `
  --stack-name $CORE_STACK `
  --query 'Stacks[0].Outputs[?OutputKey==`LiveBucketName`].OutputValue' `
  --output text

aws s3 cp `
  example-import-file-stix.env `
  "s3://${LIVE_BUCKET}/connector-env/import-file-stix.env"

$ENV_FILE_ARN = "arn:aws:s3:::${LIVE_BUCKET}/connector-env/import-file-stix.env"
```

### 7.4 Connectorスタックをデプロイ

bash:

```bash
CONNECTOR_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
echo "CONNECTOR_ID=$CONNECTOR_ID  # 再デプロイ時も同じ値を使う"

aws cloudformation deploy \
  --region "$AWS_REGION" \
  --stack-name opencti-connector-import-file-stix \
  --template-file opencti-connector.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    CoreStackName="$CORE_STACK" \
    ConnectorName=import-file-stix \
    ConnectorImage=opencti/connector-import-file-stix:latest \
    ConnectorId="$CONNECTOR_ID" \
    ConnectorType=INTERNAL_IMPORT_FILE \
    ConnectorDisplayName=ImportFileStix \
    'ConnectorScope=application/json,text/xml' \
    ConnectorTokenSecretArn="$CONNECTOR_TOKEN_SECRET_ARN" \
    ConnectorTokenJsonKey=token \
    ConnectorEnvironmentFileArn="$ENV_FILE_ARN"
```

PowerShell（Connectorは実行時生成のIDやARNを含むため、`--parameter-overrides`にKey=Value列挙で渡す）:

```powershell
$CONNECTOR_ID = [guid]::NewGuid().ToString()
"CONNECTOR_ID=$CONNECTOR_ID  # 再デプロイ時も同じ値を使う"

aws cloudformation deploy `
  --region $AWS_REGION `
  --stack-name opencti-connector-import-file-stix `
  --template-file opencti-connector.yaml `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    "CoreStackName=$CORE_STACK" `
    ConnectorName=import-file-stix `
    ConnectorImage=opencti/connector-import-file-stix:latest `
    "ConnectorId=$CONNECTOR_ID" `
    ConnectorType=INTERNAL_IMPORT_FILE `
    ConnectorDisplayName=ImportFileStix `
    "ConnectorScope=application/json,text/xml" `
    "ConnectorTokenSecretArn=$CONNECTOR_TOKEN_SECRET_ARN" `
    ConnectorTokenJsonKey=token `
    "ConnectorEnvironmentFileArn=$ENV_FILE_ARN"
```

`ConnectorId`はスタック再作成時も同じ値を再利用
変更するとOpenCTIから別Connectorとして認識される

#### 検証を挟まず直接取り込みたい場合

S3上のenvファイルを書き換える

```powershell
# ローカルのenvファイルで CONNECTOR_VALIDATE_BEFORE_IMPORT=false に変更してからアップロード
aws s3 cp example-import-file-stix.env "s3://${LIVE_BUCKET}/connector-env/import-file-stix.env"
```

**envファイルはタスク起動時にしか読まれない** 反映するにはECSサービスを強制再デプロイしてタスクを入れ替える

```powershell
aws ecs update-service --region $AWS_REGION --cluster "${PROJECT}-cluster" --service opencti-connector-import-file-stix --force-new-deployment
```


```text
Data → Data import → Global files → アップロード → example-stix-bundle.json
```

これが取り込めれば、**基盤（Platform／Worker／RabbitMQ／Connector）は正常**で、問題は元ファイルの形式側にあると確定

| 結果 | 判断 |
|---|---|
| `example-stix-bundle.json`は取り込めた | 基盤は正常。元ファイルの形式／MIMEタイプ側の問題 |
| これも取り込めない | 基盤側の問題。次項のログ確認へ |

##### XMLファイルを扱いたい場合の注意

`ImportFileStix`はOpenCTIのネイティブ形式である**STIX 2.1 JSON**を前提とした取り込みが基本
STIX 1.x のXML（例：Mandiant APT1レポートの`Appendix_G_IOCs_Full.xml`）はレガシー形式であり、確実に取り込めない**まずはSTIX 2.1 JSONへ変換してから取り込むこと**

MIMEタイプの不一致が疑われる場合は、`ConnectorScope`へ`application/xml`を追加して再デプロイする方法（`.xml`が`text/xml`ではなく`application/xml`として判定されるケースの回避）


| 症状 | 原因 | 対処 |
|---|---|---|
| Connectorログが無反応 | スコープ不一致。`ConnectorScope`（既定`application/json,text/xml`）とアップロードしたファイルのMIMEタイプが合っていない | `.json`形式のSTIX Bundleで試す。または`ConnectorScope`を見直す |
| Connectorは処理済み、Workerが無反応 | WorkerがRabbitMQのキューを消費できていない | Workerログの接続エラーを確認。`OpenCTIWorkerDesiredCount`が0でないかも確認 |
| Workerに認証エラー | Worker用Tokenの不整合 | Workerは`OpenCTIAdminSecret`の`token`を使用。コアスタックの`OpenCTIAdminToken`と一致しているか確認 |

手順7の`Validate this workbench`を実行後、次の画面で反映を確認

```text
Analyses    → Reports 以外の各画面
Observations → Indicators   → "Validation test domain"
Arsenal      → Malware      → "ValidationTestMalware"
Settings     → Organizations → "OpenCTI Deployment Validation"
```

Worker側の処理状況もログで確認

```powershell
aws logs tail "/ecs/${PROJECT}/worker" --region $AWS_REGION --since 10m
```

## 8. 外部Feed Connector

MITRE、CISA KEVなどの外部Import Connectorも同じ`opencti-connector.yaml`で常駐ECS Serviceとして追加

```text
Connector ECS Task
  ├─ http://platform.opencti.local:4000 → OpenCTI GraphQL API
  ├─ Amazon MQ:5671                    → 非同期処理
  └─ NAT Gateway:443                   → GitHub、CISA、MITRE、Vendor Feed
```

公式Connectorの多くはコンテナ内部で実行間隔を管理するため、EventBridge Scheduled Taskではなく、`DesiredCount=1`のECS Serviceとして動かす
1回処理して終了する自作Importerだけ、EventBridge Scheduler＋ECS RunTaskへ分ける方が適切

## 9. データ保持

### OpenSearch

- 初期値：`r7g.large.search`×1、gp3 300GB、Replica 0
- OpenCTIのSTIX Knowledge Graphは原則オンライン保持
- 使用率70%到達前にEBS拡張またはノードサイズ変更
- OpenSearch Serviceの自動Snapshotは短期復旧用
- 長期Snapshotリポジトリ登録はOpenSearch API操作が必要であり、このCloudFormationには含めていない

### Redis

- `maxmemory-policy=noeviction`を専用パラメータグループで設定済み
- OpenCTIはRedisにイベントストリームを保持するため、キー退避を無効化してデータ破損を防ぐ

### S3

- Live Bucket：OpenCTIが直接使用。Glacierへ移動しない
- Archive Bucket：STIX Bundle、記事原文、Manifest、手動Snapshot用
- Archive Bucketは90日後Glacier Flexible Retrieval、365日後Deep Archive
- Archive Bucketへのデータコピー／Export処理は別途実装が必要


## 参考

- QinetiQ Cyber Intelligence OpenCTI Terraform: https://github.com/QinetiQ-Cyber-Intelligence/OpenCTI-Terraform
- OpenCTI deployment overview: https://docs.opencti.io/latest/deployment/overview/
- OpenCTI configuration: https://docs.opencti.io/latest/deployment/configuration/
- OpenCTI connectors: https://docs.opencti.io/latest/deployment/connectors/
- Amazon MQ RabbitMQ version management: https://docs.aws.amazon.com/amazon-mq/latest/developer-guide/rabbitmq-version-management.html
- Session Manager Plugin install: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

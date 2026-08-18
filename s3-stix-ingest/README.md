# S3 → Lambda → OpenCTI STIX取り込みパイプライン

S3バケットにSTIXバンドル（JSON）を置くと、S3イベントでLambdaが起動し、OpenCTIの取り込みパイプラインへ自動投入する仕組みです。CloudFormationは使わず、AWS CLIで手動構築する手順をまとめています。

```
S3（取り込み専用バケット）
  └─ incoming/*.json を置く
       └─ ObjectCreated イベント
            └─ Lambda（VPC内・App Subnet）
                 ├─ Secrets Manager から APIトークン取得
                 ├─ S3 からバンドル読み込み
                 └─ POST http://platform.opencti.local:4000/graphql
                      └─ stixBundlePush → RabbitMQ push キュー
                           └─ 既存 Worker ×2 が取り込み → Knowledge Base
```

## 仕組みの要点

投入には OpenCTI 6.3.0 で追加された `stixBundlePush` ミューテーションを使います。

```graphql
mutation StixBundlePush($connectorId: String!, $bundle: String!) {
  stixBundlePush(connectorId: $connectorId, bundle: $bundle)
}
```

**新しいコネクターのデプロイは不要です。** OpenCTIの「コネクター」は *登録レコード＋RabbitMQキュー* と *実行コンテナ* に分かれており、このAPIが必要とするのは前者だけです。バンドルは `push_<connectorId>` キューへ入り、**既存のWorkerが処理**します。ImportFileStixのコンテナは一切関与しないため、そのUUIDを流用しても通常動作を妨げません。

ワークベンチ（Analyst workbench）は経由せず、直接取り込まれます。

## ⚠️ 重要：取り込み用バケットは新規に作ること

**既存の Live Bucket（OpenCTI本体が使うバケット）にS3イベント通知を付けてはいけません。**

Live Bucket は OpenCTI のオブジェクトストア（`MINIO__BUCKET_NAME`）で、アップロードファイル・エクスポート・ワークベンチのデータが常時書き込まれます。ここにトリガーを設定すると:

- OpenCTIの内部書き込みすべてでLambdaが起動する
- Lambdaが投入 → OpenCTIがLive Bucketへ保存 → 再びLambda起動、という**無限ループ**に陥る

必ず専用バケットを新規作成し、さらに `incoming/` プレフィックスと `.json` サフィックスで絞り込みます。

## 事前確認

| 項目 | 確認内容 |
|---|---|
| コアスタック | `opencti-core` がデプロイ済みで稼働中 |
| ImportFileStix Connector | デプロイ済みでUIに `Active` 表示されている |
| API用アカウント | OpenCTI上で作成済み、トークン払い出し済み |
| **CONNECTORAPI ケイパビリティ** | **API用アカウントのロールに付与されていること**（`Settings → Security → Roles`）。無いと `stixBundlePush` が権限エラーになる |
| **CONNECTOR_ID** | ImportFileStixデプロイ時に生成したUUID（後述の取得方法を参照） |

### ⚠️ CONNECTOR_ID と 管理者トークンを取り違えないこと

本手順では**2つの異なる値**をLambdaへ渡します。どちらもUUID形式に見えることがあり混同しやすいため、必ず区別してください。

| Lambdaの設定 | 渡す値 | 正体 | 取り違えやすい値 |
|---|---|---|---|
| `CONNECTOR_ID` 環境変数 | ImportFileStixの**コネクターID** | 「どのpushキューへ流すか」を示す**識別子** | `$OPENCTI_ADMIN_TOKEN` |
| `TOKEN_SECRET_ARN`（Secret内の値） | **API用アカウントのトークン** | 「誰として実行するか」を示す**認証情報** | 同上 |

特に紛らわしいのが `$OPENCTI_ADMIN_TOKEN` です。これはコアスタックのデプロイ時に `[guid]::NewGuid().ToString()` で生成した**管理者の認証トークン**であり、Connectorスタックのデプロイ時に同じ方法で生成した `CONNECTOR_ID` とは**まったくの別物**です。生成方法が同じというだけで、用途も値も異なります。

CloudFormationテンプレート上でも経路は完全に分かれています。

```text
OpenCTIAdminToken ─→ Adminシークレット ─→ Platform の APP__ADMIN__TOKEN
                                      └─→ Worker   の OPENCTI_TOKEN
ConnectorId       ─→ Connectorタスクの CONNECTOR_ID（識別子）
ConnectorTokenSecretArn ─→ Connectorタスクの OPENCTI_TOKEN（Connector専用の認証情報）
```

つまり **「認証トークン」と「コネクターID」は別レイヤー**です。本手順のLambdaは両方を使いますが、それぞれ別の値を指定します。

```text
CONNECTOR_ID     = ImportFileStix の UUID      ← 識別子。どのpushキューに流すか
TOKEN_SECRET_ARN = API用アカウントのトークン    ← 認証。誰として実行するか
```

> 管理者トークンでも技術的には動作します（管理者は CONNECTORAPI を含む全ケイパビリティを持つため）。ただし影響範囲が最大の資格情報を自動処理に使うのは避け、払い出し済みのAPI用アカウントのトークンを使用してください。

### CONNECTOR_ID の取得方法

いずれの方法でも同じUUIDが返ります。方法1と方法2の結果が一致することを確認すると確実です。

**方法1：CloudFormationスタックのパラメータから（最も確実）**

```powershell
aws cloudformation describe-stacks --region $AWS_REGION --stack-name opencti-connector-import-file-stix --query 'Stacks[0].Parameters[?ParameterKey==`ConnectorId`].ParameterValue' --output text
```

**方法2：ECSタスク定義の環境変数から**

```powershell
aws ecs describe-task-definition --region $AWS_REGION --task-definition opencti-connector-import-file-stix --query "taskDefinition.containerDefinitions[0].environment[?name=='CONNECTOR_ID'].value" --output text
```

**方法3：OpenCTIのUIから**

`Data → Ingestion → Connectors → ImportFileStix` を開くと、ブラウザのURL末尾がそのコネクターのIDになっています。

> **組み込みコネクターは指定しないでください。** UIの一覧には `[FILE] CSV Mapper import` や `[DRAFT] Draft validation` といったOpenCTI内部処理用のコネクターも表示されますが、これらのキューへ外部からバンドルを流すのは想定外の使い方です。指定すべきは**自分でデプロイした（＝自分でUUIDを生成した）コネクター**のIDだけです。

## 構築手順

以降は PowerShell（Windows）を前提としています。

> **PowerShellの引用符に注意**: PowerShellはネイティブコマンドへ渡す際、単一引用符内でも二重引用符を削除します。本手順ではJSONを `ConvertTo-Json` で生成し `file://` 経由で渡すことで、この問題を回避しています。

### 0. 変数の準備

```powershell
$AWS_REGION   = "us-west-1"
$CORE_STACK   = "opencti-core"
$PROJECT      = "opencti"
$FUNC_NAME    = "opencti-s3-stix-ingest"
$ROLE_NAME    = "opencti-s3-ingest-lambda-role"

# 識別子：ImportFileStix の コネクターID（既存のものを流用）
# ※ $OPENCTI_ADMIN_TOKEN とは別物。前掲の「取得方法」で確認すること
$CONNECTOR_ID = "<ImportFileStixのUUID>"

# 認証情報：API用アカウントのトークン（管理者トークンではない）
$OPENCTI_API_TOKEN = "<払い出し済みのトークン>"
```

`$CONNECTOR_ID` は次のコマンドで直接代入することもできます。

```powershell
$CONNECTOR_ID = aws cloudformation describe-stacks --region $AWS_REGION --stack-name opencti-connector-import-file-stix --query 'Stacks[0].Parameters[?ParameterKey==`ConnectorId`].ParameterValue' --output text
$CONNECTOR_ID
```

コアスタックからネットワーク情報を取得します。

```powershell
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
$VPC_ID = aws cloudformation describe-stacks --region $AWS_REGION --stack-name $CORE_STACK --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' --output text
$APP_SUBNET_ID = aws cloudformation describe-stacks --region $AWS_REGION --stack-name $CORE_STACK --query 'Stacks[0].Outputs[?OutputKey==`AppSubnetId`].OutputValue' --output text
$APP_SG_ID = aws cloudformation describe-stacks --region $AWS_REGION --stack-name $CORE_STACK --query 'Stacks[0].Outputs[?OutputKey==`AppSecurityGroupId`].OutputValue' --output text
"VPC=$VPC_ID / Subnet=$APP_SUBNET_ID / AppSG=$APP_SG_ID"
```

### 1. 取り込み専用S3バケットを作成

バケット名はグローバルで一意である必要があるため、アカウントIDを付与します。

```powershell
$INGEST_BUCKET = "opencti-stix-ingest-$ACCOUNT_ID"

aws s3api create-bucket --bucket $INGEST_BUCKET --region $AWS_REGION --create-bucket-configuration "LocationConstraint=$AWS_REGION"
aws s3api put-public-access-block --bucket $INGEST_BUCKET --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
aws s3api put-bucket-encryption --bucket $INGEST_BUCKET --server-side-encryption-configuration '{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"AES256\"}}]}'
```

> `us-east-1` の場合のみ `--create-bucket-configuration` は不要です（指定するとエラーになります）。

### 2. APIトークンをSecrets Managerへ登録

```powershell
@{ token = $OPENCTI_API_TOKEN } | ConvertTo-Json -Compress |
  ForEach-Object { [System.IO.File]::WriteAllText("$PWD\token.json", $_) }

aws secretsmanager create-secret --region $AWS_REGION --name /opencti/s3-ingest/token --secret-string file://token.json
Remove-Item token.json

$TOKEN_SECRET_ARN = aws secretsmanager describe-secret --region $AWS_REGION --secret-id /opencti/s3-ingest/token --query ARN --output text
```

登録内容を必ず検証します。`token` プロパティが表示されれば正常です。

```powershell
aws secretsmanager get-secret-value --region $AWS_REGION --secret-id /opencti/s3-ingest/token --query SecretString --output text | ConvertFrom-Json
```

### 3. DLQ（デッドレターキュー）を作成

Lambdaがリトライ後も失敗した場合の退避先です。

```powershell
aws sqs create-queue --region $AWS_REGION --queue-name "$FUNC_NAME-dlq"
$DLQ_URL = aws sqs get-queue-url --region $AWS_REGION --queue-name "$FUNC_NAME-dlq" --query QueueUrl --output text
$DLQ_ARN = aws sqs get-queue-attributes --region $AWS_REGION --queue-url $DLQ_URL --attribute-names QueueArn --query 'Attributes.QueueArn' --output text
```

### 4. IAMロールを作成

```powershell
$trust = @{
  Version = "2012-10-17"
  Statement = @(@{ Effect="Allow"; Principal=@{ Service="lambda.amazonaws.com" }; Action="sts:AssumeRole" })
} | ConvertTo-Json -Depth 5 -Compress
[System.IO.File]::WriteAllText("$PWD\trust-policy.json", $trust)

aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document file://trust-policy.json
aws iam attach-role-policy --role-name $ROLE_NAME --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
Remove-Item trust-policy.json
```

`AWSLambdaVPCAccessExecutionRole` はENI管理とCloudWatch Logsへの書き込みを含みます。加えて、S3読み取り・Secret取得・DLQ送信を許可します。

```powershell
$policy = @{
  Version = "2012-10-17"
  Statement = @(
    @{ Effect="Allow"; Action=@("s3:GetObject"); Resource="arn:aws:s3:::$INGEST_BUCKET/*" },
    @{ Effect="Allow"; Action=@("secretsmanager:GetSecretValue"); Resource=$TOKEN_SECRET_ARN },
    @{ Effect="Allow"; Action=@("sqs:SendMessage"); Resource=$DLQ_ARN }
  )
} | ConvertTo-Json -Depth 6 -Compress
[System.IO.File]::WriteAllText("$PWD\lambda-policy.json", $policy)

aws iam put-role-policy --role-name $ROLE_NAME --policy-name opencti-s3-ingest --policy-document file://lambda-policy.json
Remove-Item lambda-policy.json
```

### 5. セキュリティグループを作成し、OpenCTIへの疎通を許可

Lambda用のSGを作り、そこからのみ Platform の 4000番へ入れるようにします。

```powershell
$LAMBDA_SG_ID = aws ec2 create-security-group --region $AWS_REGION --group-name "$FUNC_NAME-sg" --description "Lambda for S3 STIX ingest" --vpc-id $VPC_ID --query GroupId --output text

aws ec2 authorize-security-group-ingress --region $AWS_REGION --group-id $APP_SG_ID --protocol tcp --port 4000 --source-group $LAMBDA_SG_ID
```

新規SGは既定でアウトバウンド全許可のため、Lambda側の送信設定は追加不要です。

### 6. Lambda関数をデプロイ

```powershell
Compress-Archive -Path lambda_function.py -DestinationPath function.zip -Force

aws lambda create-function --region $AWS_REGION `
  --function-name $FUNC_NAME `
  --runtime python3.13 `
  --handler lambda_function.handler `
  --role "arn:aws:iam::${ACCOUNT_ID}:role/$ROLE_NAME" `
  --zip-file fileb://function.zip `
  --timeout 120 `
  --memory-size 1024 `
  --vpc-config "SubnetIds=$APP_SUBNET_ID,SecurityGroupIds=$LAMBDA_SG_ID" `
  --dead-letter-config "TargetArn=$DLQ_ARN" `
  --environment "Variables={OPENCTI_URL=http://platform.opencti.local:4000,CONNECTOR_ID=$CONNECTOR_ID,TOKEN_SECRET_ARN=$TOKEN_SECRET_ARN}"
```

> IAMロールの伝播に数秒かかるため、直後の実行が `The role defined for the function cannot be assumed` で失敗することがあります。その場合は少し待って再実行してください。

同時実行数を絞り、OpenCTI側の過負荷を防ぎます。

```powershell
aws lambda put-function-concurrency --region $AWS_REGION --function-name $FUNC_NAME --reserved-concurrent-executions 2
```

### 7. S3イベント通知を設定

まずS3からLambdaを呼び出す権限を付与します。

```powershell
aws lambda add-permission --region $AWS_REGION --function-name $FUNC_NAME --statement-id s3invoke --action lambda:InvokeFunction --principal s3.amazonaws.com --source-arn "arn:aws:s3:::$INGEST_BUCKET" --source-account $ACCOUNT_ID
```

次に通知設定です。`incoming/` かつ `.json` のみに限定します。

```powershell
$LAMBDA_ARN = aws lambda get-function --region $AWS_REGION --function-name $FUNC_NAME --query 'Configuration.FunctionArn' --output text

$notif = @{
  LambdaFunctionConfigurations = @(
    @{
      LambdaFunctionArn = $LAMBDA_ARN
      Events = @("s3:ObjectCreated:*")
      Filter = @{ Key = @{ FilterRules = @(
        @{ Name = "prefix"; Value = "incoming/" },
        @{ Name = "suffix"; Value = ".json" }
      )}}
    }
  )
} | ConvertTo-Json -Depth 10 -Compress
[System.IO.File]::WriteAllText("$PWD\notification.json", $notif)

aws s3api put-bucket-notification-configuration --region $AWS_REGION --bucket $INGEST_BUCKET --notification-configuration file://notification.json
Remove-Item notification.json
```

## 動作確認

リポジトリ同梱の検証用バンドル（Identity・Malware・Indicator・Relationshipの4オブジェクト）を投入します。

```powershell
aws s3 cp ../example/example-stix-bundle.json "s3://$INGEST_BUCKET/incoming/test-bundle.json"
```

Lambdaのログを確認します。

```powershell
aws logs tail "/aws/lambda/$FUNC_NAME" --region $AWS_REGION --since 5m --follow
```

`Queued 4 objects from s3://.../incoming/test-bundle.json for ingestion` が出れば投入成功です。続いてWorker側を確認します。

```powershell
aws logs tail "/ecs/$PROJECT/worker" --region $AWS_REGION --since 5m
```

最後にOpenCTIのUIで反映を確認します。

```text
Data → Ingestion → Connectors → ImportFileStix → Works（投入したWorkが表示される）
Observations → Indicators   → "Validation test domain"
Arsenal      → Malware      → "ValidationTestMalware"
```

> 検証用オブジェクトは合成データです。確認後は削除してください。

## 運用上の注意

- **重複投入**: S3イベントは at-least-once 配信のため、同一ファイルで複数回起動する場合があります。OpenCTIはSTIX ID単位でupsertするため実害は出ませんが、Work履歴には重複が残ります
- **バンドルサイズ**: 既定で50MiBを超えるオブジェクトは明示的にエラーにしています（`MAX_BUNDLE_BYTES` で変更可）。大きなバンドルはGraphQLのペイロード上限に達する可能性があるため、分割して投入してください
- **ファイルの保持**: 投入後もS3上のファイルは残ります。ライフサイクルルールで一定期間後に削除するか、`incoming/` から移動する運用を推奨します
- **費用**: Lambda・S3・SQSはいずれも従量課金で、低頻度の利用であれば月数十円程度に収まります。VPC接続のLambdaはENIを使いますが、ENI自体に課金はありません

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| Lambdaがタイムアウトする | VPC設定漏れ、またはSGで4000番が開いていない | App Subnetに配置されているか、手順5のIngressが入っているか確認 |
| `FORBIDDEN_ACCESS` / 権限エラー | API用アカウントに **CONNECTORAPI** ケイパビリティが無い | `Settings → Security → Roles` でロールに付与 |
| `Connector not found` 系のエラー | `CONNECTOR_ID` が実在しない。**`$OPENCTI_ADMIN_TOKEN` を誤って指定している**ケースが多い（どちらもUUID形式のため） | 「CONNECTOR_ID の取得方法」で正しい値を再取得 |
| Secrets Manager呼び出しでタイムアウト | NAT経由の疎通不良 | App Subnetの既定ルートがNAT Gatewayを指しているか確認 |
| Lambdaは成功するがデータが入らない | Worker側で処理が滞留 | Workerログを確認。`Connected workers` が1以上か確認 |
| Lambdaが大量に起動する | Live Bucketにトリガーを付けてしまっている | 専用バケットに付け直す（本書冒頭の警告を参照） |
| `HTTP 413` などペイロードエラー | バンドルが大きすぎる | ファイルを分割して投入 |

## 撤去手順

```powershell
aws s3api put-bucket-notification-configuration --region $AWS_REGION --bucket $INGEST_BUCKET --notification-configuration "{}"
aws lambda delete-function --region $AWS_REGION --function-name $FUNC_NAME
aws ec2 revoke-security-group-ingress --region $AWS_REGION --group-id $APP_SG_ID --protocol tcp --port 4000 --source-group $LAMBDA_SG_ID
aws ec2 delete-security-group --region $AWS_REGION --group-id $LAMBDA_SG_ID
aws iam delete-role-policy --role-name $ROLE_NAME --policy-name opencti-s3-ingest
aws iam detach-role-policy --role-name $ROLE_NAME --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
aws iam delete-role --role-name $ROLE_NAME
aws sqs delete-queue --region $AWS_REGION --queue-url $DLQ_URL
aws secretsmanager delete-secret --region $AWS_REGION --secret-id /opencti/s3-ingest/token --force-delete-without-recovery
```

S3バケットは中身を空にしてから削除します。

```powershell
aws s3 rm "s3://$INGEST_BUCKET" --recursive
aws s3api delete-bucket --bucket $INGEST_BUCKET --region $AWS_REGION
```

VPC接続のLambdaを削除した直後はENIの解放に数分かかるため、セキュリティグループの削除が `DependencyViolation` で失敗することがあります。その場合は時間をおいて再実行してください。

## 参考

- OpenCTI GraphQL API: https://docs.opencti.io/latest/reference/api/
- stixBundlePush を追加したPR: https://github.com/OpenCTI-Platform/opencti/pull/7705
- 元issue（外部システムからのバンドル投入）: https://github.com/OpenCTI-Platform/opencti/issues/7696

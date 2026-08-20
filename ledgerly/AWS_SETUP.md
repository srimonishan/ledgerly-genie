# Ledgerly — AWS Setup (Bedrock + SageMaker, optional)

Phase 0 runs completely fine with **zero AWS setup** — the local scikit-learn
fallback trains the forecast model, and the templated-sentence fallback
explains cash risk. This doc is only for turning on the two AWS-backed
upgrades:

- **Bedrock** → the Predicted Cash Risk panel's plain-English explanation
  comes from a real LLM instead of the templated sentence.
- **SageMaker** → the cash forecast model trains as a SageMaker training
  job instead of in-process scikit-learn.

Both are opt-in via env vars (`LEDGERLY_USE_BEDROCK=true`,
`LEDGERLY_USE_SAGEMAKER=true`) — nothing here happens automatically.

**Cost reality check:** with a ~$200 / 6-month credit budget, both of
these are pennies if you follow this doc (Bedrock Haiku calls are
fractions of a cent each; one SageMaker training job on `ml.m5.large` for
this dataset size runs a few minutes, well under $1). The risk isn't the
happy path, it's leaving something running. Section 5 covers cleanup —
read it before you start, not after.

**Important constraint:** a Databricks Free Edition workspace is
Databricks-managed infrastructure, not deployed inside your AWS account.
There is no IAM role you can "attach" to a Databricks App or Job the way
you would to an EC2 instance. Instead, the app/job needs actual AWS
**access key + secret** credentials, injected as environment variables.
Section 4 covers doing that safely via Databricks secrets rather than
plaintext.

---

## 1. Prerequisites

- An AWS account with the credits/budget mentioned above.
- The [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  installed locally, or just use the AWS Console — both paths given below.
- Decide your region up front and use it consistently everywhere in this
  doc (Bedrock model availability varies by region — `us-east-1` and
  `us-west-2` have the broadest model access as of this writing; check the
  Bedrock console's **Model access** page for your account before
  committing).

---

## 2. S3 bucket (for SageMaker training data + model artifacts)

Only needed if you're using the SageMaker path.

**Console:** S3 → **Create bucket** → name it something like
`ledgerly-ml-<your-account-id>` (bucket names are globally unique, so
plain `ledgerly-ml` will likely be taken) → same region as above → leave
everything else default (block all public access ON) → **Create bucket**.

**CLI equivalent:**
```bash
aws s3 mb s3://ledgerly-ml-<your-account-id> --region <your-region>
```

This is your `LEDGERLY_S3_BUCKET` value.

---

## 3. IAM role for SageMaker (the training job's execution role)

This is the role **SageMaker itself** assumes to run the training
container — separate from the credentials your script uses to *submit*
the job (that's Section 4).

**Console:**
1. IAM → **Roles** → **Create role**.
2. Trusted entity type: **AWS service** → Use case: **SageMaker** →
   **SageMaker - Execution**.
3. Attach policy: `AmazonSageMakerFullAccess` (fastest path for a
   hackathon-scale project; if you want to scope it down, a custom policy
   limited to `s3:GetObject`/`s3:PutObject`/`s3:ListBucket` on your
   specific bucket plus `logs:CreateLogGroup`/`CreateLogStream`/`PutLogEvents`
   is sufficient — SageMaker training doesn't need anything broader here).
4. Name it `ledgerly-sagemaker-execution-role` → **Create role**.
5. Open the role, copy its **ARN** (looks like
   `arn:aws:iam::<account-id>:role/ledgerly-sagemaker-execution-role`).

This is your `AWS_ROLE_ARN` value.

---

## 4. IAM user (the credentials your script/job/app authenticates as)

This is what actually calls the AWS APIs — submitting the SageMaker
training job, and/or invoking Bedrock. Scope it to only what it needs.

**Console:**
1. IAM → **Users** → **Create user** → name `ledgerly-app`. Do **not**
   enable console access — this is programmatic-only.
2. **Attach policies directly** → **Create policy** → JSON tab, paste:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "SageMakerTrainingSubmit",
         "Effect": "Allow",
         "Action": [
           "sagemaker:CreateTrainingJob",
           "sagemaker:DescribeTrainingJob",
           "sagemaker:StopTrainingJob"
         ],
         "Resource": "*"
       },
       {
         "Sid": "PassExecutionRole",
         "Effect": "Allow",
         "Action": "iam:PassRole",
         "Resource": "arn:aws:iam::<account-id>:role/ledgerly-sagemaker-execution-role"
       },
       {
         "Sid": "S3TrainingData",
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
         "Resource": [
           "arn:aws:s3:::ledgerly-ml-<your-account-id>",
           "arn:aws:s3:::ledgerly-ml-<your-account-id>/*"
         ]
       },
       {
         "Sid": "BedrockInvoke",
         "Effect": "Allow",
         "Action": "bedrock:InvokeModel",
         "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"
       }
     ]
   }
   ```
   (Drop the `PassExecutionRole`/`S3TrainingData`/`SageMakerTrainingSubmit`
   statements if you only want Bedrock, or drop `BedrockInvoke` if you only
   want SageMaker.)
3. Name the policy `ledgerly-app-policy`, create it, attach it to the
   `ledgerly-app` user.
4. Back on the user → **Security credentials** tab → **Create access key**
   → use case **Application running outside AWS** → create it → **download
   the CSV now** (the secret is shown once, never again).

These two values (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) are
credentials, not placeholders you paste into `app.yaml` in plaintext. Two
ways to use them, pick based on where you're running:

**Running `02_train_cash_forecast_model.py` locally (simplest, recommended
first):**
```bash
export AWS_ACCESS_KEY_ID=<from the CSV>
export AWS_SECRET_ACCESS_KEY=<from the CSV>
export AWS_REGION=<your-region>
export AWS_ROLE_ARN=arn:aws:iam::<account-id>:role/ledgerly-sagemaker-execution-role
export LEDGERLY_S3_BUCKET=ledgerly-ml-<your-account-id>
export LEDGERLY_USE_SAGEMAKER=true
python3 02_train_cash_forecast_model.py
```
This only writes `data/cash_forecast.csv` locally though — you still need
to also run it as a Databricks Job (below) to update the real
`cash_forecast` Delta table the app reads.

**Running inside Databricks (the Job, and/or the App for Bedrock) — use
Databricks secrets, never a plaintext env var in `app.yaml`:**
```bash
databricks secrets create-scope ledgerly-aws
databricks secrets put-secret ledgerly-aws aws-access-key-id --string-value "<from the CSV>"
databricks secrets put-secret ledgerly-aws aws-secret-access-key --string-value "<from the CSV>"
```
- **For the Job** (SageMaker training): in the Job task's configuration,
  add environment variables `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_REGION`, `AWS_ROLE_ARN`, `LEDGERLY_S3_BUCKET`,
  `LEDGERLY_USE_SAGEMAKER=true`, referencing the secrets via the Jobs UI's
  "reference a secret" option for the two credential values (do not type
  the raw key into the env var value field).
- **For the App** (Bedrock): same idea — in `app.yaml`, reference secrets
  instead of plaintext:
  ```yaml
  env:
    - name: "AWS_ACCESS_KEY_ID"
      valueFrom: "ledgerly-aws/aws-access-key-id"
    - name: "AWS_SECRET_ACCESS_KEY"
      valueFrom: "ledgerly-aws/aws-secret-access-key"
    - name: "AWS_REGION"
      value: "<your-region>"
    - name: "LEDGERLY_USE_BEDROCK"
      value: "true"
  ```
  (Confirm the exact `valueFrom` secret-reference syntax against your
  workspace's current Databricks Apps docs — this schema has moved before;
  the Apps UI's "Add environment variable" → "Reference a secret" flow
  will generate the correct block for you if this one doesn't validate.)

---

## 5. Enable Bedrock model access

Bedrock models are opt-in per account/region — `InvokeModel` fails with an
access-denied error until you request access once.

1. AWS Console → **Bedrock** → **Model access** (left sidebar) → **Modify
   model access** (or **Manage model access**).
2. Find **Anthropic → Claude 3 Haiku** (matches the `modelId` already in
   `app.py`) → check it → **Request model access**. This is usually
   instant for Anthropic models, no waiting period.
3. Confirm the exact model ID string in the console matches
   `anthropic.claude-3-haiku-20240307-v1:0` in `app.py` — Bedrock
   occasionally requires an inference-profile ARN instead of the bare
   model ID depending on region/model; if `InvokeModel` errors with
   something like "on-demand throughput isn't supported," copy the
   inference profile ARN shown in the console and use that as the
   `modelId` value in `app.py` instead.

---

## 6. Test before wiring through Databricks

Verify both integrations locally first — much faster feedback loop than
debugging through a Databricks Job/App deploy cycle.

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=...

# SageMaker (see full env var list in Section 4)
export AWS_ROLE_ARN=... LEDGERLY_S3_BUCKET=... LEDGERLY_USE_SAGEMAKER=true
python3 02_train_cash_forecast_model.py
# expect: "[sagemaker] submitting training job..." then a completion message,
# and a REMINDER that no endpoint was created.

# Bedrock — quick standalone check
python3 -c "
import boto3, json
c = boto3.client('bedrock-runtime', region_name='<your-region>')
r = c.invoke_model(
    modelId='anthropic.claude-3-haiku-20240307-v1:0',
    body=json.dumps({'anthropic_version':'bedrock-2023-05-31','max_tokens':50,
                      'messages':[{'role':'user','content':'Say hi in 5 words.'}]}))
print(json.loads(r['body'].read())['content'][0]['text'])
"
```

Only once both of those work locally should you push the env
vars/secrets into the Databricks Job and App per Section 4.

---

## 7. Cleanup checklist (do this after your demo, and periodically while building)

- [ ] **SageMaker → Training jobs**: confirm no jobs are stuck
      "InProgress" longer than expected (they self-terminate; this is just
      a sanity check).
- [ ] **SageMaker → Endpoints**: this list should always be **empty** — the
      code in this repo never creates one, so anything here means it was
      created manually while experimenting. Delete it:
      `aws sagemaker delete-endpoint --endpoint-name <name>`.
- [ ] **S3**: the training bucket only holds small CSVs and a model
      artifact per run — no cleanup strictly needed, but you can empty old
      runs under `ledgerly/cash-forecast/train/` if you're being tidy.
- [ ] **Billing → Bedrock/SageMaker line items**: spot-check after each
      session that usage is at the "few cents" scale you expect, not
      climbing — the earliest signal something is misconfigured (e.g. a
      loop accidentally re-invoking Bedrock) is a cost spike, not an error
      message.
- [ ] **IAM access key**: if you're pausing work on this for a while,
      consider deactivating (not deleting) the `ledgerly-app` access key
      in IAM until you resume — it costs nothing to leave active, but
      deactivating unused credentials is good hygiene.

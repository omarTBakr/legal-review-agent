# Examples of files that are gitignored

Everything here is a placeholder with no real values. The real files live next
to the code and are listed in `.gitignore`, so they are never committed.

| Example | Copy to | What it is |
| --- | --- | --- |
| `private_ssh_aws/ec2-ssh-key.pem`, `private_ssh_aws/id_ed25519` | `private_ssh_aws/` | SSH keys for reaching a server |
| `aws/credentials` | `aws/credentials` | credentials for the `aws` CLI (the app reads `.env`) |
| `../.env.example` | `.env` | the application's settings |

```bash
cp -r .example/private_ssh_aws private_ssh_aws
cp -r .example/aws aws
cp .env.example .env
```

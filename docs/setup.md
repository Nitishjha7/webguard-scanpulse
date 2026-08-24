# WebGuard (ScanPulse) — Project Setup Steps

## Step 1: Naya folder + git init
```bash
cd ~/Music
mkdir webguard-scanpulse
cd webguard-scanpulse
git init
```

## Step 2: Folder structure banao (empty, phase-wise doc ke hisaab se)
```bash
mkdir -p backend/app/blueprints
mkdir -p backend/app/engines
mkdir -p backend/app/models
mkdir -p backend/migrations
mkdir -p celery_worker
mkdir -p frontend/src
mkdir -p docs

touch backend/requirements.txt
touch backend/Dockerfile
touch backend/app/__init__.py
touch celery_worker/Dockerfile
touch frontend/Dockerfile
touch docker-compose.yml
touch README.md
touch .gitignore
```

## Step 3: .gitignore banao
```bash
cat > .gitignore << 'EOF'
venv/
__pycache__/
*.pyc
node_modules/
dist/
.env
*.db
instance/
*.log
EOF
```

## Step 4: README likho
```bash
echo "# WebGuard (ScanPulse) — Website Health, SSL & DNS Security Monitoring Platform" > README.md
```

## Step 5: Pehla commit (main branch)
```bash
git branch -M main
git add .
git commit -m "Initial project scaffold"
```

## Step 6: GitHub pe remote repo bana kar link karo

Pehle GitHub.com pe jaake naya repo banao (bina README/gitignore ke, kyunki already hai), fir:

```bash
git remote add origin git@github-personal:Nitishjha7/webguard-scanpulse.git
git push -u origin main
```

(Same SSH alias jo pichli baar kaam kiya tha)

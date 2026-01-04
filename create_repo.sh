#!/bin/bash
# ==========================================
# GitHub Project Auto-Creator Script
# by Dave (guidasworld)
# ==========================================

# Exit if any command fails
set -e

# sudo apt install gh -y

# gh auth login

echo ""
echo "=========================================="
echo "🔧 GitHub Repository Auto Creator"
echo "=========================================="
echo ""

# --- Ask for repository name ---
read -p "Enter new repository name: " REPO_NAME

# --- Ask for project visibility ---
read -p "Should it be private? (y/N): " IS_PRIVATE
if [[ "$IS_PRIVATE" =~ ^[Yy]$ ]]; then
    VISIBILITY="--private"
else
    VISIBILITY="--public"
fi

# --- Ask for .gitignore and README creation ---
read -p "Add Python .gitignore and README.md? (Y/n): " ADD_FILES
if [[ "$ADD_FILES" =~ ^[Nn]$ ]]; then
    ADD_README=""
else
    ADD_README="--add-readme --gitignore Python"
fi

# --- Confirm project directory ---
read -p "Enter project folder path (default: current directory): " PROJECT_PATH
PROJECT_PATH=${PROJECT_PATH:-$(pwd)}

cd "$PROJECT_PATH"

echo ""
echo "📁 Using project directory: $PROJECT_PATH"
echo ""

# --- Initialize Git if not already initialized ---
if [ ! -d ".git" ]; then
    echo "⚙️ Initializing local Git repository..."
    git init
fi

# --- Add all project files ---
git add .
git commit -m "Initial commit" || true

# --- Create repo on GitHub ---
echo ""
echo "🌐 Creating GitHub repository '$REPO_NAME'..."

# --- Ensure README.md and .gitignore exist ---
[ ! -f README.md ] && echo "# $REPO_NAME" > README.md
[ ! -f .gitignore ] && curl -s https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore -o .gitignore

git add README.md .gitignore
git commit -m "Add README.md and Python .gitignore" || true


gh repo create "$REPO_NAME" $VISIBILITY --source=. --remote=origin --push


# --- Verify setup ---
echo ""
echo "✅ Repository created and pushed successfully!"
echo "📦 Remote linked: $(git remote -v | head -1 | awk '{print $2}')"
echo ""
echo "💻 You can now run 'git push' and 'git pull' normally."
echo "=========================================="

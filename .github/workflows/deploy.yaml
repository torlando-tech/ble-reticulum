name: Deploy to Raspberry Pi

on:
  workflow_run:
    workflows: ["Tests"]
    types:
      - completed
  workflow_dispatch:

jobs:
  # ============================================================================
  # JOB 1: Parse PI_HOSTS into matrix for parallel deployment
  # ============================================================================
  setup:
    name: Setup Deployment Matrix
    runs-on: ubuntu-latest
    # Only run if tests passed (for workflow_run) or if manually triggered
    if: ${{ github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success' }}
    outputs:
      matrix: ${{ steps.set-matrix.outputs.matrix }}
      branch: ${{ steps.get-branch.outputs.branch }}

    steps:
      - name: Validate required secrets
        run: |
          if [ -z "${{ secrets.PI_HOSTS }}" ]; then
            echo "Error: PI_HOSTS secret is not set"
            echo "Please set PI_HOSTS secret with comma-separated hostnames (e.g., 'pi1.local,pi2.local')"
            exit 1
          fi
          if [ -z "${{ secrets.PI_REPO_PATH }}" ]; then
            echo "Error: PI_REPO_PATH secret is not set"
            echo "Please set PI_REPO_PATH secret with repository path (e.g., '/home/pi/ble-reticulum')"
            exit 1
          fi
          if [ -z "${{ secrets.PI_USER }}" ]; then
            echo "Error: PI_USER secret is not set"
            echo "Please set PI_USER secret with SSH username (e.g., 'pi')"
            exit 1
          fi
          if [ -z "${{ secrets.PI_SSH_KEY }}" ]; then
            echo "Error: PI_SSH_KEY secret is not set"
            echo "Please set PI_SSH_KEY secret with SSH private key for Pi access"
            exit 1
          fi
          echo "✓ All required secrets are configured"

      - name: Get branch name
        id: get-branch
        run: |
          BRANCH="${{ github.event.workflow_run.head_branch || github.ref_name }}"
          echo "branch=$BRANCH" >> $GITHUB_OUTPUT
          echo "Deployment branch: $BRANCH"

      - name: Parse PI_HOSTS into deployment matrix
        id: set-matrix
        env:
          PI_HOSTS: ${{ secrets.PI_HOSTS }}
        run: |
          # Split comma-separated PI_HOSTS into array
          IFS=',' read -ra HOSTS <<< "$PI_HOSTS"

          # Build JSON array for matrix
          JSON='['
          for i in "${!HOSTS[@]}"; do
            HOST=$(echo "${HOSTS[$i]}" | xargs)
            if [ $i -gt 0 ]; then JSON+=','; fi
            JSON+="{\"host\":\"$HOST\",\"index\":$i}"
          done
          JSON+=']'

          echo "matrix=$JSON" >> $GITHUB_OUTPUT
          echo "Deployment matrix created for ${#HOSTS[@]} Pi(s)"
          echo "$JSON" | jq '.'

  # ============================================================================
  # JOB 2: Deploy to each Pi (parallel matrix execution)
  # ============================================================================
  deploy:
    name: Deploy to Pi ${{ matrix.pi.index }} (${{ matrix.pi.host }})
    runs-on: self-hosted
    needs: setup
    strategy:
      matrix:
        pi: ${{ fromJson(needs.setup.outputs.matrix) }}
      fail-fast: false  # Continue deploying to other Pis if one fails

    steps:
      - name: Setup SSH key
        env:
          PI_SSH_KEY: ${{ secrets.PI_SSH_KEY }}
        run: |
          mkdir -p ~/.ssh
          chmod 700 ~/.ssh
          echo "$PI_SSH_KEY" > ~/.ssh/id_ed25519
          chmod 600 ~/.ssh/id_ed25519

          cat >> ~/.ssh/config <<EOF
          Host *.local 10.0.0.* 192.168.*
              StrictHostKeyChecking no
              UserKnownHostsFile /dev/null
              LogLevel ERROR
          EOF
          chmod 600 ~/.ssh/config

      - name: Deploy to ${{ matrix.pi.host }}
        env:
          PI_HOST: ${{ matrix.pi.host }}
          PI_REPO_PATH: ${{ secrets.PI_REPO_PATH }}
          PI_USER: ${{ secrets.PI_USER }}
          BRANCH_NAME: ${{ needs.setup.outputs.branch }}
        run: |
          echo "==================================="
          echo "Deploying to Pi ${{ matrix.pi.index }}"
          echo "==================================="
          echo "Host: $PI_HOST"
          echo "Branch: $BRANCH_NAME"
          echo "Repository: $PI_REPO_PATH"
          echo "==================================="
          echo ""

          # Deployment script
          DEPLOY_SCRIPT="set -e
          echo '  [1/8] Navigating to repository...'
          cd '$PI_REPO_PATH' || exit 1

          echo '  [2/8] Fetching latest changes...'
          git fetch --all || exit 1

          echo '  [3/8] Checking out branch: $BRANCH_NAME...'
          git checkout '$BRANCH_NAME' || exit 1

          echo '  [4/8] Pulling latest code...'
          git pull || exit 1

          echo '  [5/8] Creating ~/.reticulum/interfaces directory...'
          mkdir -p ~/.reticulum/interfaces || exit 1

          echo '  [6/8] Copying interface files...'
          cp -v src/RNS/Interfaces/*.py ~/.reticulum/interfaces/ || exit 1

          echo '  [7/8] Stopping rnsd and clearing logs...'
          RNSD_BIN=\"\$HOME/.local/bin/rnsd\"
          if systemctl is-active --quiet rnsd 2>/dev/null; then
            sudo systemctl stop rnsd || exit 1
            echo '  ✓ rnsd stopped via systemd'
          else
            pkill -9 rnsd 2>/dev/null || true
            sleep 1
          fi
          # Clear the log file for clean validation
          echo '' > ~/.reticulum/logfile
          echo '  ✓ Log file cleared'

          echo '  [8/8] Starting rnsd...'
          if systemctl is-active --quiet rnsd.service 2>/dev/null || systemctl is-enabled --quiet rnsd.service 2>/dev/null; then
            sudo systemctl start rnsd || exit 1
            echo '  ✓ rnsd started via systemd'
          else
            nohup \"\$RNSD_BIN\" -s > /dev/null 2>&1 &
            sleep 2
            if pgrep -x rnsd > /dev/null; then
              echo '  ✓ rnsd started successfully'
            else
              echo '  ✗ Failed to start rnsd'
              exit 1
            fi
          fi

          echo '  ✓ Deployment successful!'"

          # Execute deployment via SSH
          if echo "$DEPLOY_SCRIPT" | ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$PI_USER@$PI_HOST" bash; then
            echo ""
            echo "✓ Successfully deployed to $PI_HOST"
          else
            echo ""
            echo "✗ Failed to deploy to $PI_HOST"
            exit 1
          fi

      - name: Cleanup SSH key
        if: always()
        run: rm -f ~/.ssh/id_ed25519

  # ============================================================================
  # JOB 3: Validate BLE interface on each Pi (parallel matrix execution)
  # ============================================================================
  validate:
    name: Validate Pi ${{ matrix.pi.index }} (${{ matrix.pi.host }})
    runs-on: self-hosted
    needs: [setup, deploy]
    strategy:
      matrix:
        pi: ${{ fromJson(needs.setup.outputs.matrix) }}
      fail-fast: false

    steps:
      - name: Setup SSH key
        env:
          PI_SSH_KEY: ${{ secrets.PI_SSH_KEY }}
        run: |
          mkdir -p ~/.ssh
          chmod 700 ~/.ssh
          echo "$PI_SSH_KEY" > ~/.ssh/id_ed25519
          chmod 600 ~/.ssh/id_ed25519

      - name: Validate BLE interface on ${{ matrix.pi.host }}
        env:
          PI_HOST: ${{ matrix.pi.host }}
          PI_USER: ${{ secrets.PI_USER }}
        run: |
          echo "==================================="
          echo "Validating Pi ${{ matrix.pi.index }}"
          echo "==================================="
          echo "Host: $PI_HOST"
          echo "==================================="
          echo ""

          # Validation script
          VALIDATION_SCRIPT='set -e

          echo "  [1/4] Waiting for startup (5s)..."
          sleep 5

          echo "  [2/4] Checking rnsd process..."
          if ! pgrep -x rnsd > /dev/null; then
            echo "  ✗ rnsd process not running"
            exit 1
          fi
          echo "  ✓ rnsd is running (PID: $(pgrep -x rnsd))"

          echo "  [3/4] Checking BLE interface logs..."
          LOG_FILE="$HOME/.reticulum/logfile"

          if [ ! -f "$LOG_FILE" ]; then
            echo "  ✗ Log file not found at $LOG_FILE"
            exit 1
          fi

          # Retry 3 times with 3s delay
          SUCCESS=false
          for attempt in 1 2 3; do
            STARTUP_LOGS=$(head -200 "$LOG_FILE" 2>/dev/null || echo "")

            # Check for critical errors
            if echo "$STARTUP_LOGS" | grep -qE "(failed to start driver|Timeout waiting for Transport)"; then
              echo "  ✗ BLE driver/identity error detected"
              echo ""
              echo "  Startup error logs:"
              head -100 "$LOG_FILE" | grep -E "(BLE|ERROR)"
              exit 1
            fi

            # Check for success
            if echo "$STARTUP_LOGS" | grep -q "interface online"; then
              echo "  ✓ BLE interface online"
              SUCCESS=true
              break
            fi

            if [ $attempt -lt 3 ]; then
              echo "    Retry $attempt/3 (waiting 3s)..."
              sleep 3
            fi
          done

          if [ "$SUCCESS" = false ]; then
            echo "  ✗ Interface did not come online after 3 attempts"
            echo ""
            echo "  Startup logs:"
            head -100 "$LOG_FILE" | grep -E "(BLE|ERROR|WARNING)"
            exit 1
          fi

          echo "  [4/4] Checking Bluetooth adapter..."
          if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
            ADAPTER_MAC=$(bluetoothctl show 2>/dev/null | grep "Address:" | awk "{print \$2}")
            echo "  ✓ Bluetooth adapter powered ($ADAPTER_MAC)"
          else
            echo "  ⚠ Bluetooth adapter status unknown"
          fi

          echo ""
          echo "  ✓ Validation successful!"
          '

          # Execute validation via SSH
          if echo "$VALIDATION_SCRIPT" | ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$PI_USER@$PI_HOST" bash; then
            echo ""
            echo "✓ $PI_HOST validation passed"
          else
            echo ""
            echo "✗ $PI_HOST validation failed"
            exit 1
          fi

      - name: Cleanup SSH key
        if: always()
        run: rm -f ~/.ssh/id_ed25519

  # ============================================================================
  # JOB 4: Summary (runs after all deploy + validate jobs complete)
  # ============================================================================
  summary:
    name: Deployment Summary
    runs-on: ubuntu-latest
    needs: [setup, deploy, validate]
    if: always()

    steps:
      - name: Generate summary
        run: |
          echo "## 🎉 Deployment Complete" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "**Branch:** ${{ needs.setup.outputs.branch }}" >> $GITHUB_STEP_SUMMARY
          echo "**Commit:** ${{ github.sha }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY

          if [ "${{ needs.deploy.result }}" == "success" ] && [ "${{ needs.validate.result }}" == "success" ]; then
            echo "### ✅ All Pis Deployed and Validated Successfully" >> $GITHUB_STEP_SUMMARY
          else
            echo "### ⚠️ Some Pis Failed" >> $GITHUB_STEP_SUMMARY
            echo "" >> $GITHUB_STEP_SUMMARY
            if [ "${{ needs.deploy.result }}" != "success" ]; then
              echo "- **Deploy:** ${{ needs.deploy.result }}" >> $GITHUB_STEP_SUMMARY
            fi
            if [ "${{ needs.validate.result }}" != "success" ]; then
              echo "- **Validate:** ${{ needs.validate.result }}" >> $GITHUB_STEP_SUMMARY
            fi
            echo "" >> $GITHUB_STEP_SUMMARY
            echo "Check individual job logs for details." >> $GITHUB_STEP_SUMMARY
          fi

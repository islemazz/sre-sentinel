pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    environment {
        IMAGE            = "sre-sentinel:ci-${env.BUILD_NUMBER}"
        GITLEAKS_VERSION = '8.30.1'
        TRIVY_IMAGE      = 'ghcr.io/aquasecurity/trivy:0.65.0'
    }

    stages {
        stage('Test') {
            steps {
                sh '''
                    python3 -m venv .venv
                    . .venv/bin/activate
                    python -m pip install --quiet -r requirements-dev.txt
                    pytest -q --cov --cov-report=xml --cov-report=term --junitxml=pytest-report.xml
                '''
            }
            post {
                always {
                    junit 'pytest-report.xml'
                }
            }
        }

        stage('Dependency audit') {
            steps {
                // Known vulnerabilities in the libraries listed in requirements.txt
                sh '''
                    . .venv/bin/activate
                    python -m pip install --quiet pip-audit==2.10.1
                    pip-audit -r requirements.txt
                '''
            }
        }

        stage('Secret scan') {
            steps {
                // Passwords or tokens committed to Git (whole history). The binary is
                // downloaded, then checked against the published checksum before use.
                sh '''
                    mkdir -p .tools
                    BASE="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
                    TARBALL="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
                    curl -fsSL -o ".tools/${TARBALL}" "${BASE}/${TARBALL}"
                    curl -fsSL -o .tools/checksums.txt "${BASE}/gitleaks_${GITLEAKS_VERSION}_checksums.txt"
                    (cd .tools && grep " ${TARBALL}\\$" checksums.txt | sha256sum -c -)
                    tar -xzf ".tools/${TARBALL}" -C .tools gitleaks
                    .tools/gitleaks git --no-banner --redact -v .
                '''
            }
        }

        stage('SonarQube analysis') {
            steps {
                script {
                    def scannerHome = tool 'sonar-scanner'
                    withSonarQubeEnv('SonarQube') {
                        sh "${scannerHome}/bin/sonar-scanner"
                    }
                }
            }
        }

        stage('Quality Gate') {
            steps {
                timeout(time: 5, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }

        stage('Build image') {
            steps {
                sh 'docker build -t "$IMAGE" .'
            }
        }

        stage('Image scan') {
            steps {
                // Trivy runs as a container inside the private Docker engine and
                // reads the image through that engine's own socket.
                sh '''
                    SCAN="docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache $TRIVY_IMAGE image --no-progress --ignore-unfixed"
                    echo "--- Report: HIGH and CRITICAL (does not fail the build)"
                    $SCAN --severity HIGH,CRITICAL "$IMAGE"
                    echo "--- Gate: any fixable CRITICAL fails the build"
                    $SCAN --severity CRITICAL --exit-code 1 "$IMAGE"
                '''
            }
        }

        stage('Smoke test') {
            steps {
                // Same hardening as docker-compose.yml. The container lives in the
                // private engine, reachable from Jenkins as http://docker:8000
                sh '''
                    docker rm -f sentinel-ci >/dev/null 2>&1 || true
                    docker run -d --name sentinel-ci -p 8000:8000 \
                        --read-only --tmpfs /tmp --cap-drop ALL \
                        --security-opt no-new-privileges:true \
                        -v sentinel-ci-data:/data "$IMAGE"
                    bash scripts/smoke_test.sh http://docker:8000 || { docker logs sentinel-ci; exit 1; }
                    uid="$(docker exec sentinel-ci id -u)"
                    echo "container user id: $uid"
                    test "$uid" != "0"
                '''
            }
        }
    }

    post {
        always {
            sh 'docker rm -f sentinel-ci >/dev/null 2>&1 || true'
        }
    }
}
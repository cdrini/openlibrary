pipeline {
    agent { docker { image 'node:12' } }

    stages {
        stage('Install') { steps { sh 'npm install' } }
        stage('Pre-Build & Build') {
            parallel {
                stage('Lint') { steps { sh 'npm run lint:js' } }
                stage('Unit Test') { steps { sh 'npm run test:js' } }
                stage('Build') { steps { sh 'npm run build-assets:js' } }
            }
        }
        stage('Test Build') { steps { sh 'npx bundlesize' } }
    }
}

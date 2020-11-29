pipeline {
    // Custom docker image which contains Node and Python
    agent { docker { image 'openlibrary/olbase:latest' } }

    stages {
        stage('Install') {
            parallel {
                stage('Python') { steps { sh 'pip install -r requirements_test.txt' } }
                stage('Node') { steps { sh 'npm install' } }
            }
        }
        stage('Lint') {
            parallel {
                stage('Python') { steps { sh 'make lint' } }
                stage('JS') { steps { sh 'npm run lint:js' } }
                stage('CSS') { steps { sh 'npm run lint:css' } }
            }
        }
        stage('Unit Tests') {
            parallel {
                stage('Python') { steps { sh 'make test-py' } }
                stage('JS') { steps { sh 'npm run test:js' } }
            }
        }
        stage('Build') {
            parallel {
                stage('JS') { steps { sh 'make js' } }
                stage('CSS') { steps { sh 'make css' } }
                stage('Components') { steps { sh 'make components' } }
                stage('i18n') { steps { sh 'make i18n' } }
            }
        }
        stage('Test Build') { steps { sh 'npx bundlesize' } }
    }
}

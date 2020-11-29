pipeline {
    agent { docker { image 'openlibrary/olbase:latest' } }

    stages {
        stage('Prep') {
            // Need infogami to be master sometimes; let's do it using env
            // variables
            steps {
                // Remove the symlink in this directory; that's what makes it available
                sh 'rm infogami'
                // Make a master version
                sh '''
                    cp -r vendor/infogami vendor/infogami-master
                    cd vendor/infogami-master
                    git pull origin master
                '''

                // Also: Don't need node_modules from scratch; use the one in the docker image
                sh "ln -s /openlibrary/node_modules ${env.WORKSPACE}/node_modules"
            }
        }
        stage('Install') {
            parallel {
                stage('Python 2') {
                    environment { PYENV_VERSION = '2.7.6' }
                    steps { sh 'pip install -r requirements_test.txt' }
                }
                stage('Python 3') {
                    environment { PYENV_VERSION = '3.8.6' }
                    steps { sh 'pip install -r requirements_test.txt' }
                }
                stage('Node') { steps { sh 'npm install' } }
            }
        }
        stage('Lint') {
            parallel {
                stage('Python 2') {
                    environment { PYENV_VERSION = '2.7.6' }
                    steps { sh 'make lint' }
                }
                stage('Python 3') {
                    environment { PYENV_VERSION = '3.8.6' }
                    steps { sh 'make lint' }
                }
                stage('JS') { steps { sh 'npm run lint:js' } }
                stage('CSS') { steps { sh 'npm run lint:css' } }
            }
        }
        stage('Unit Tests') {
            parallel {
                stage('Python 2 + Infogami prod') {
                    environment { PYENV_VERSION = '2.7.6' }
                    steps { sh "PYTHONPATH='${env.WORKSPACE}/vendor/infogami' make test-py" }
                }
                stage('Python 2 + Infogami master') {
                    environment { PYENV_VERSION = '2.7.6' }
                    steps { sh "PYTHONPATH='${env.WORKSPACE}/vendor/infogami-master' make test-py" }
                }
                stage('Python 3') {
                    environment { PYENV_VERSION = '3.8.6' }
                    steps { sh "PYTHONPATH='${env.WORKSPACE}/vendor/infogami-master' make test-py" }
                }
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

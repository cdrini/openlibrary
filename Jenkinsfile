pipeline {
    // Custom docker image which contains Node and Python
    agent { docker { image 'openlibrary/olbase:latest' } }

    stages {
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
        stage('Pre-build & Build') {
            matrix {
                axes {
                    axis {
                        name 'VERB'
                        values 'lint', 'unit-test', 'build'
                    }
                    axis {
                        name 'OBJECT'
                        values 'python', 'js', 'css', 'components', 'i18n'
                    }
                }
                excludes {
                    exclude {
                        axis {
                            name 'VERB'
                            values 'lint'
                        }
                        axis {
                            name 'OBJECT'
                            values 'components', 'i18n'
                        }
                    }
                    exclude {
                        axis {
                            name 'VERB'
                            values 'unit-test'
                        }
                        axis {
                            name 'OBJECT'
                            values 'css', 'components', 'i18n'
                        }
                    }
                    exclude {
                        axis {
                            name 'VERB'
                            values 'build'
                        }
                        axis {
                            name 'OBJECT'
                            values 'python'
                        }
                    }
                }
                stages {
                    stage('Pre-build or Build') {
                        steps {
                            sh "make ${VERB}-${OBJECT}"
                        }
                    }
                }
            }
        }
        stage('Test Build') { steps { sh 'npx bundlesize' } }
    }
}

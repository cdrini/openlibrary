
## Solr reindex from OL dump

This goes through building a solr instance from a dump file. To build the reindex (only tested on the OJF environment):

### Steps
1. Start Jenkins (altering the details as necessary):
```bash
docker run \
  -u root \
  -d \
  -p 8080:8080 \
  -p 50000:50000 \
  -v jenkins-data:/var/jenkins_home \
  -v /storage:/storage \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/docker/volumes/jenkins-data:/var/lib/docker/volumes/jenkins-data \
  --restart always \
  --name jenkins \
  jenkinsci/blueocean
```
2. Follow the steps here to finish setting up Jenkins: https://jenkins.io/doc/book/installing/#setup-wizard
3. Follow these steps to create a new Pipeline Project using git: https://jenkins.io/doc/book/pipeline/getting-started/#defining-a-pipeline-in-scm
    - Be sure to set the script path to the `Jenkinsfile` in this directory (e.g. `scripts/solr_builder/Jenkinsfile`)
4. Run the pipeline!

Notes:
- Jenkins has a classic UI and a new, Blue Ocean UI. Each has its benefits.
- Logs are stored as artifacts after the jobs are finished, but you can also access the logs by clicking on "workspaces" in the classic UI and navigating to the logs directory (you can usually ignore the paths containing `@`).
- Although it _looks_ like we are starting a Jenkins docker image, which starts a building docker image, which starts the `solr_builder` docker images, because we are forwarding the docker socket, everything is actually happening with the host docker (this is best practice). This has some caveats:
    - You should not assume you have full control of what's running; if you're not careful you will stop other containers
    - You should not try to run multiple re-indexes at the same time. This might be possible, but the current pipeline makes assumptions which would cause this to error

### Possible Issues
#### The SCM field is blank / has no options
Per the "Defining A Pipeline in SCM" page above, the "Definition" section of the Pipeline should be "Pipeline script from SCM". The next step is to choose the source control system by clicking on the "SCM" field, but it might be blank.

Solution: update and restart Jenkins, which can probably be done at the top of the page in the alerts section.

#### Building the pipeline fails almost immediately (various errors)

Check the console output and look for the errors listed below.

##### WorkflowScript: 3: Invalid agent type "dockerfile" specified #####
```
org.codehaus.groovy.control.MultipleCompilationErrorsException: startup failed:
WorkflowScript: 3: Invalid agent type "dockerfile" specified. Must be one of [any, label, none] @ line 3, column 5.
       dockerfile {
       ^
```
Cause: Missing `Docker` and `Docker Pipeline` plugins.

Solution: Dashboard > Manage Jenkins > Manage Plugins. In the left-hand menu, click on "Available plugins" and search for "docker". Then install:
- Docker (possibly displaying as "Docker plugin")
- Docker Pipeline

##### java.lang.NoSuchMethodError: 'boolean org.kohsuke.groovy.sandbox.SandboxTransformer.mightBePositionalArgumentConstructor(org.codehaus.groovy.ast.expr.VariableExpression)' #####
```
java.lang.NoSuchMethodError: 'boolean org.kohsuke.groovy.sandbox.SandboxTransformer.mightBePositionalArgumentConstructor(org.codehaus.groovy.ast.expr.VariableExpression)'
	at com.cloudbees.groovy.cps.SandboxCpsTransformer.visitAssignmentOrCast(SandboxCpsTransformer.java:93)
```
Cause: Out of date Groovy Pipeline plugin:

Solution: Dashboard > Manage Jenkins > Manage Plugins. In the left-hand menu, click on "Installed plugins", search for the "Groovy Pipeline" plugin and update it.


### Editing the Jenkins Pipeline
- If you want to modify the pipeline, you can set the Jenkin's Project to use a local path. Note that you will have to restart Jenkins so that it mounts the path (i.e. `-v "$HOME:/home"`). Alternatively, you can also change the Git url to that of your fork, and specify a branch (e.g. `*/solr-builder--jenkins`) and push to the branch whenever you want to test your code. Jenkins will pull from the branch when you run the project.
- Use VS Code to write your Pipeline; it has a validation extension. See https://jenkins.io/doc/book/pipeline/development/ for other editors/tips
- Read https://jenkins.io/doc/book/pipeline/development/ for general development tips

## Sample dumps for testing

The pipeline above ingests the full Open Library dumps: ~18GB compressed, ~119M records,
several hours to import. `make_sample_dumps.py` builds a scaled-down stand-in so the
pipeline can be exercised end to end in a reasonable amount of time.

```sh
# ~1M records, streamed straight from the latest dump on archive.org
python scripts/solr_builder/make_sample_dumps.py 1000000 --out-dir /storage/ol-sample

# ...or from dumps already downloaded
python scripts/solr_builder/make_sample_dumps.py 1000000 \
    --source /storage/openlibrary --out-dir /storage/ol-sample
```

The output directory mirrors the archive.org item the Jenkinsfile downloads from --
combined dump, per-type dumps, ratings, reading log, covers metadata, and (with
`--osp-dump`) a subset `osp_totals.db` -- so `DUMP_DIR` can be pointed straight at it.
A `manifest.json` records the achieved counts and ratios next to the production ones.

Nothing needs to be downloaded up front: the script streams each per-type dump over
HTTP and keeps only what it selects, so it needs disk for the output alone. It reads
~17.7GB either way, which takes on the order of an hour.

### What the sample preserves

The sample is meant to behave like production in the ways the indexer cares about, so
the type mix, the share of editions with an `ocaid`, the share of works carrying
ratings or a reading log, and the share of orphaned editions all track the real
figures. Those come out of the sampling scheme rather than being imposed afterwards:
inclusion is decided by a hash of a key, which is independent of the fields whose
distributions matter.

References are closed: any key an included document mentions resolves to an included
document. This is not cosmetic. `WorkSolrUpdater` resolves author and series keys
through `DataProvider.get_document`, which raises when the row is missing, and the
caller catches it with a bare `except` -- so a single dangling author reference drops
the whole work from the index with nothing but a log line. `--verify` (on by default)
re-reads the finished sample and fails the run if anything dangles.

### Known deviations

- **Authors are over-represented** -- 15.2% of records on a 1M-record run, against
  production's 12.9%. Authors are shared (~2.5 works each) and a sub-1% sample mostly
  breaks that sharing up. Anchoring on authors rather than works keeps bibliographies
  together and recovers most of it; the rest would need whole connected components of
  the co-authorship graph. `--anchor work` shows the unmitigated version (~31%).
- **`other` runs high** -- 0.4% against production's 0.08%. Every non-subject record
  in that dump is kept whole, because it holds the schema types, languages and series
  that reference closure needs. It is ~3.6k records regardless of sample size, so the
  effect shrinks as the sample grows.
- **Lists keep fewer seeds than they do in production.** A list's seeds are spread
  across the whole corpus, so at these rates few survive; seeds are pruned to what is
  present and lists left with none are dropped. The number of lists still tracks
  production, but the seeds-per-list distribution skews short.
- **Ratings and reading-log rows keep their work key but may lose their edition key**
  (nulled to `\N` when that edition is outside the sample). Nothing joins on it, and
  dropping the row instead would skew the share of works carrying ratings.

The production figures the script calibrates against are in `ProdStats`, measured
2026-09-10. They drift slowly; `--prod-stats <file.json>` overrides them without a code
change.

## Final Sync

TODO. Something along the lines of: Add a solrupdater to compose.production.yaml that points to the new server, and set its offset to be the correct date. See [5493 Move production solr from solr1 to solr0](https://github.com/internetarchive/openlibrary/issues/5493) for hints.

## Deploy

Now that the solr is ready, we can dump its database and import it into the solr on production. Here is the command to do that from the solrbuilder server:

```sh
time docker run --rm \
    --volumes-from solr_builder_solr-1 \
    -v /tmp/solr:/backup \
    ubuntu:xenial \
    tar czf /backup/solrbuilder-$(date +%Y-%m-%d).tar.gz /var/solr/data
```

(Last run: 41min/14G with 2020-10 dump; OJF)

Then on the production server (ol-solr0) run:

```sh
cd /opt/openlibrary

# Copy file from solrbuilder server (4min; 2020-11-05 ol-solr0)
time scp YOU@SOLR_BUILDER_SERVER:/tmp/solr/solrbuilder-2020-03-02.tar.gz /tmp/solr/solrbuilder-2020-03-02.tar.gz

# Restore backup file (7min; 2021-08-11 ol-solr0)
# Note the name "openlibrary_solr-data" should match "{OL_DIR}_{SOLR_DATA_VOLUME}", where:
#    OL_DIR: is the name of the directory where the openlibrary repo is; likely openlibrary
#    SOLR_DATA_VOLUME: is the name of the volume the solr service uses; defined in compose.yaml
time docker run -v openlibrary_solr-data:/var/solr/data -v /tmp/solr:/backup ubuntu:xenial \
    tar xzf /backup/solrbuilder-2021-08-11.tar.gz

# Start the services
COMPOSE_FILE="compose.yaml:compose.production.yaml" HOSTNAME="$HOSTNAME"docker compose --profile=ol-solr0 up -d
```

## Resetting

In order to be able to re-run the job, you need to stop/remove any of the old containers you don't intend to reuse:

```sh
# "new" solr containers/volumes
docker rm -f -v solr_builder_solr-1
docker volume rm solr_builder_solr-data

# DB containers/volumes
docker rm -f -v solr_builder_db-1 solr_builder_adminer-1
docker volume rm solr_builder_postgres-data
```

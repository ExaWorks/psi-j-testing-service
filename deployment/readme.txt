Contains files need to deploy the service.

To make a release of the service:
- decide on a release of the service
- update the version in RELEASE to '<major>.<minor>.<patch>'
- tag with 'v<major>.<minor>.<patch>' !important;
- run 'build.sh <major>.<minor>.<patch>' to build and push docker image

- this can all be done by running release.sh; it commits the current
  RELEASE file, tags, and builds the docker image


To deploy the service(s):
- make a release as above for deployment
- edit config and fill in appropriate values if necessary
- run deploy.sh from the deploy directory. This will:
    - install or upgrade nginx
    - start or upgrade the PSI/J and SDK containers to the version
    specified in the config file
- set up the ssl certificate
    - this depends on where the certificate is obtained from
    - for letencrypt, follow the instructions provided by 
    letsencrypt

To upgrade the container:
- stop and delete the two containers running the service instances
- follow the deployment instructions above

To upgrade the service with minimal downtime:
- make a release as above
- run 'upgrade.sh <major>.<minor>.<patch>' from the deploy directory


There might be some manual work involved in initially 
getting SSL certs, but the following scheme is designed 
for automatic renewal:
- generate a self-signed certificate (you can use gen-cert.sh)
  (this is needed to get nginx running without too much messing
  with configuration files)
- start the container with run.sh <version>
- use certbot to get the actual cert; when it asks for the 
  webroot, point it to 'webroot'; this will place certs in 
  /etc/letsencrypt/live/<CN>/, so you'll need to carefully 
  symlink these into certs/certs/ssl.crt and 
  certs/private/ssl.key and restart the container; symlinking
  will not work outside of the volume mount, so the run script
  will automatically mount /etc/letsencrypt into the container 
  if it's there on the host

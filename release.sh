#!/bin/bash

set -e

VERSION=`cat RELEASE`

read -p "This will tag and create docker image for $VERSION. Continue (y/n)? " CONT

if [ "$CONT" != "y" ]; then
    echo "Aborting."
    exit 0
fi

git commit -m "Updated version to $VERSION" RELEASE

git push

git tag "v$VERSION"

git push origin "v$VERSION"

pushd deployment/docker

read -p "Build? " BUILDING

./build.sh

popd


echo "Done"
# start with delphi's python image
FROM delphi_python

# copy over all source files
COPY repos repos
RUN chmod -R o+r repos/

# configure the image to match the delphi server
RUN mv repos/delphi/covidcast-nowcast/src delphi/covidcast_nowcast

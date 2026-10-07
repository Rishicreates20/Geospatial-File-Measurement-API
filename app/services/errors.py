"""Domain errors. The API layer maps these to HTTP status codes."""


class GeoFileError(Exception):
    """Base class for problems with an uploaded file. The message is safe to show to clients."""


class UnsupportedFileTypeError(GeoFileError):
    """The file extension is not one we accept (HTTP 400)."""


class FileTooLargeError(GeoFileError):
    """The upload exceeds the configured size limit (HTTP 413)."""


class InvalidGeoFileError(GeoFileError):
    """The file was accepted but could not be read as geospatial data (status FAILED)."""

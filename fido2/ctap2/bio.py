# Copyright (c) 2020 Yubico AB
# All rights reserved.
#
#   Redistribution and use in source and binary forms, with or
#   without modification, are permitted provided that the following
#   conditions are met:
#
#    1. Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#    2. Redistributions in binary form must reproduce the above
#       copyright notice, this list of conditions and the following
#       disclaimer in the documentation and/or other materials provided
#       with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import absolute_import, unicode_literals

from .. import cbor
from ..ctap import CtapError

from enum import IntEnum, unique
import struct


class BioEnrollment(object):
    @unique
    class RESULT(IntEnum):
        MODALITY = 0x01
        FINGERPRINT_KIND = 0x02
        MAX_SAMPLES_REQUIRED = 0x03
        TEMPLATE_ID = 0x04
        LAST_SAMPLE_STATUS = 0x05
        REMAINING_SAMPLES = 0x06
        TEMPLATE_INFOS = 0x07

    @unique
    class TEMPLATE_INFO(IntEnum):
        ID = 0x01
        NAME = 0x02

    @unique
    class MODALITY(IntEnum):
        FINGERPRINT = 0x01

    @staticmethod
    def is_supported(info):
        if "bioEnroll" in info.options:
            return True
        # We also support the Prototype command
        return bool(
            "FIDO_2_1_PRE" in info.versions
            and info.options.get("credentialMgmtPreview")
        )

    def __init__(self, ctap, modality):
        if not self.is_supported(ctap.info):
            raise ValueError("Authenticator does not support BioEnroll")

        self.ctap = ctap
        self.modality = self.get_modality()
        if modality != self.modality:
            raise ValueError("Device does not support {:s}".format(modality))

    def get_modality(self):
        """Get bio modality.

        :return: The type of modality supported by the authenticator.
        """
        return self.ctap.bio_enrollment(get_modality=True)[
            BioEnrollment.RESULT.MODALITY
        ]


class CaptureError(Exception):
    def __init__(self, code):
        self.code = code
        super(CaptureError, self).__init__(f"Fingerprint capture error: {code}")


class FPEnrollmentContext(object):
    """Helper object to perform fingerprint enrollment.

    :param bio: An instance of FPBioEnrollment.
    :param timeout: Optional timeout for fingerprint captures (ms).
    :ivar remaining: The number of (estimated) remaining samples needed.
    :ivar template_id: The ID of the new template (only available after the initial
        sample has been captured).
    """

    def __init__(self, bio, timeout=None):
        self._bio = bio
        self.timeout = timeout
        self.template_id = None
        self.remaining = None

    def capture(self, event=None, on_keepalive=None):
        """Capture a fingerprint sample.

        This call will block for up to timeout milliseconds (or indefinitely, if
        timeout not specified) waiting for the user to scan their fingerprint to
        collect one sample.

        :return: None, if more samples are needed, or the template ID if enrollment is
            completed.
        """
        if self.template_id is None:
            self.template_id, status, self.remaining = self._bio.enroll_begin(
                self.timeout, event, on_keepalive
            )
        else:
            status, self.remaining = self._bio.enroll_capture_next(
                self.template_id, self.timeout, event, on_keepalive
            )
        if status != FPBioEnrollment.FEEDBACK.FP_GOOD:
            raise CaptureError(status)
        return self.template_id if self.remaining == 0 else None

    def cancel(self):
        """Cancels ongoing enrollment."""
        self._bio.enroll_cancel()
        self.template_id = None


class FPBioEnrollment(BioEnrollment):
    """Implementation of a draft specification of the bio enrollment API.
    WARNING: This specification is not final and this class is likely to change.

    NOTE: The get_fingerprint_sensor_info method does not require authentication, and
    can be used by setting pin_uv_protocol and pin_uv_token to None.

    :param ctap: An instance of a CTAP2 object.
    :param pin_uv_protocol: The PIN/UV protocol version used.
    :param pin_uv_token: A valid PIN/UV Auth Token for the current CTAP session.
    """

    @unique
    class CMD(IntEnum):
        ENROLL_BEGIN = 0x01
        ENROLL_CAPTURE_NEXT = 0x02
        ENROLL_CANCEL = 0x03
        ENUMERATE_ENROLLMENTS = 0x04
        SET_NAME = 0x05
        REMOVE_ENROLLMENT = 0x06
        GET_SENSOR_INFO = 0x07

    @unique
    class PARAM(IntEnum):
        TEMPLATE_ID = 0x01
        TEMPLATE_NAME = 0x02
        TIMEOUT_MS = 0x03

    @unique
    class FEEDBACK(IntEnum):
        FP_GOOD = 0x00
        FP_TOO_HIGH = 0x01
        FP_TOO_LOW = 0x02
        FP_TOO_LEFT = 0x03
        FP_TOO_RIGHT = 0x04
        FP_TOO_FAST = 0x05
        FP_TOO_SLOW = 0x06
        FP_POOR_QUALITY = 0x07
        FP_TOO_SKEWED = 0x08
        FP_TOO_SHORT = 0x09
        FP_MERGE_FAILURE = 0x0A
        FP_EXISTS = 0x0B
        FP_DATABASE_FULL = 0x0C
        NO_USER_ACTIVITY = 0x0D
        NO_UP_TRANSITION = 0x0E

        def __str__(self):
            return "0x%02X - %s" % (self.value, self.name)

    def __init__(self, ctap, pin_uv_protocol, pin_uv_token):
        super(FPBioEnrollment, self).__init__(ctap, BioEnrollment.MODALITY.FINGERPRINT)
        self.pin_uv_protocol = pin_uv_protocol
        self.pin_uv_token = pin_uv_token

    def _call(self, sub_cmd, params=None, auth=True, event=None, on_keepalive=None):
        if params is not None:
            params = {k: v for k, v in params.items() if v is not None}
        kwargs = {
            "modality": self.modality,
            "sub_cmd": sub_cmd,
            "sub_cmd_params": params,
            "event": event,
            "on_keepalive": on_keepalive,
        }
        if auth:
            msg = struct.pack(">BB", self.modality, sub_cmd)
            if params is not None:
                msg += cbor.encode(params)
            kwargs["pin_uv_protocol"] = self.pin_uv_protocol.VERSION
            kwargs["pin_uv_param"] = self.pin_uv_protocol.authenticate(
                self.pin_uv_token, msg
            )
        return self.ctap.bio_enrollment(**kwargs)

    def get_fingerprint_sensor_info(self):
        """Get fingerprint sensor info.

        :return: A dict containing FINGERPRINT_KIND and MAX_SAMPLES_REQUIRES.
        """
        return self._call(FPBioEnrollment.CMD.GET_SENSOR_INFO, auth=False)

    def enroll_begin(self, timeout=None, event=None, on_keepalive=None):
        """Start fingerprint enrollment.

        Starts the process of enrolling a new fingerprint, and will wait for the user
        to scan their fingerprint once to provide an initial sample.

        :param timeout: Optional timeout in milliseconds.
        :return: A tuple containing the new template ID, the sample status, and the
            number of samples remaining to complete the enrollment.
        """
        result = self._call(
            FPBioEnrollment.CMD.ENROLL_BEGIN,
            {FPBioEnrollment.PARAM.TIMEOUT_MS: timeout},
            event=event,
            on_keepalive=on_keepalive,
        )
        return (
            result[BioEnrollment.RESULT.TEMPLATE_ID],
            FPBioEnrollment.FEEDBACK(result[BioEnrollment.RESULT.LAST_SAMPLE_STATUS]),
            result[BioEnrollment.RESULT.REMAINING_SAMPLES],
        )

    def enroll_capture_next(
        self, template_id, timeout=None, event=None, on_keepalive=None
    ):
        """Continue fingerprint enrollment.

        Continues enrolling a new fingerprint and will wait for the user to scan their
        fingerpring once to provide a new sample.
        Once the number of samples remaining is 0, the enrollment is completed.

        :param template_id: The template ID returned by a call to `enroll_begin`.
        :param timeout: Optional timeout in milliseconds.
        :return: A tuple containing the sample status, and the number of samples
            remaining to complete the enrollment.
        """
        result = self._call(
            FPBioEnrollment.CMD.ENROLL_CAPTURE_NEXT,
            {
                FPBioEnrollment.PARAM.TEMPLATE_ID: template_id,
                FPBioEnrollment.PARAM.TIMEOUT_MS: timeout,
            },
            event=event,
            on_keepalive=on_keepalive,
        )
        return (
            FPBioEnrollment.FEEDBACK(result[BioEnrollment.RESULT.LAST_SAMPLE_STATUS]),
            result[BioEnrollment.RESULT.REMAINING_SAMPLES],
        )

    def enroll_cancel(self):
        """Cancel any ongoing fingerprint enrollment."""
        self._call(FPBioEnrollment.CMD.ENROLL_CANCEL, auth=False)

    def enroll(self, timeout=None):
        """Convenience wrapper for doing fingerprint enrollment.

        See FPEnrollmentContext for details.
        :return: An initialized FPEnrollmentContext.
        """
        return FPEnrollmentContext(self, timeout)

    def enumerate_enrollments(self):
        """Get a dict of enrolled fingerprint templates which maps template ID's to
        their friendly names.

        :return: A dict of enrolled template_id -> name pairs.
        """
        try:
            return {
                t[BioEnrollment.TEMPLATE_INFO.ID]: t[BioEnrollment.TEMPLATE_INFO.NAME]
                for t in self._call(FPBioEnrollment.CMD.ENUMERATE_ENROLLMENTS)[
                    BioEnrollment.RESULT.TEMPLATE_INFOS
                ]
            }
        except CtapError as e:
            if e.code == CtapError.ERR.INVALID_OPTION:
                return {}
            raise

    def set_name(self, template_id, name):
        """Set/Change the friendly name of a previously enrolled fingerprint template.

        :param template_id: The ID of the template to change.
        :param name: A friendly name to give the template.
        """
        self._call(
            FPBioEnrollment.CMD.SET_NAME,
            {
                BioEnrollment.TEMPLATE_INFO.ID: template_id,
                BioEnrollment.TEMPLATE_INFO.NAME: name,
            },
        )

    def remove_enrollment(self, template_id):
        """Remove a previously enrolled fingerprint template.

        :param template_id: The Id of the template to remove.
        """
        self._call(
            FPBioEnrollment.CMD.REMOVE_ENROLLMENT,
            {BioEnrollment.TEMPLATE_INFO.ID: template_id},
        )

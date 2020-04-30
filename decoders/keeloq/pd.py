##
## This file is part of the libsigrokdecode project.
##
## Copyright (C) 2014 Torsten Duwe <duwe@suse.de>
## Copyright (C) 2014 Sebastien Bourdelin <sebastien.bourdelin@savoirfairelinux.com>
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, see <http://www.gnu.org/licenses/>.
##

import sigrokdecode as srd
import logging

logger = logging.getLogger('keeloq')
fh = logging.FileHandler('keeloq.log', mode='a', encoding=None, delay=False)
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
fh.setLevel(logging.DEBUG)
logger.addHandler(fh)

class SamplerateError(Exception):
    pass

class KeeloqStates():
    state_bit = None
    state_byte = None
    state_portion =None
    last_period_samples = 0
    bit_count = 0
    byte_count = 0
    next_byte = 0
    next_portion = ""

    bit_position = 0
    byte_position = 0
    portion_position = 0
    
class Decoder(srd.Decoder):
    api_version = 3
    id = 'keeloq'
    name = 'KeeLoq'
    longname = 'Keeloq Pulse-width demodulation'
    desc = 'Decodes the Keeloq Portions, i.e. encrypted word, remote serial number, button, repeat and battery flags'
    license = 'gplv2+'
    inputs = ['logic']
    outputs = []
    tags = ['Security/crypto', 'Wireless/RF']
    channels = (
        {'id': 'data', 'name': 'Data', 'desc': 'Data line'},
    )
    options = (
        {'id': 'polarity', 'desc': 'Polarity', 'default': 'active-high',
            'values': ('active-low', 'active-high')},
    )
    annotations = (
        ('pwmbit', 'PWM Bit'),
        ('keeloqhex', 'Keeloq Byte'),
        ('keeloqportion', 'Keeloq Portion'),
    )
    annotation_rows = (
         ('pwmbits', 'PWM Bits', (0,)),
         ('keeloqhexs', 'Keeloq Bytes', (1,)),
         ('keeloqportions', 'Keeloq Portions', (2,)),
    )
    binary = (
        ('raw', 'RAW file'),
    )
    
    keeloq_states = None

    def __init__(self):
        self.reset()
        self.keeloq_states = KeeloqStates()

    def reset(self):
        self.samplerate = None
        self.ss_block = self.es_block = None

    def metadata(self, key, value):
        if key == srd.SRD_CONF_SAMPLERATE:
            self.samplerate = value

    def start(self):
        #logger.error("start")
        self.out_ann = self.register(srd.OUTPUT_ANN)
        self.out_binary = self.register(srd.OUTPUT_BINARY)
        self.out_average = \
            self.register(srd.OUTPUT_META,
                          meta=(float, 'Average', 'PWM base (cycle) frequency'))

    def put_portion(self, data):
        self.put(self.keeloq_states.portion_position, self.es_block, self.out_ann, [2, [data]])

    def put_hex(self, data):
        self.put(self.keeloq_states.byte_position, self.es_block, self.out_ann, [1, [data]])

    def put_bit(self, bit):
        self.put(self.keeloq_states.bit_position, self.es_block, self.out_ann, [0, [bit]])

    def putb(self, data):
        self.put(self.ss_block, self.es_block, self.out_binary, data)

    def decode(self):
        #logger.error("decode")
        if not self.samplerate:
            raise SamplerateError('Cannot decode without samplerate.')

        num_cycles = 0
        average = 0
        duty_range = range(0,0)
        period_low = 0
        period_high = 0

        # Wait for an "active" edge (depends on config). This starts
        # the first full period of the inspected signal waveform.
        self.wait({0: 'f' if self.options['polarity'] == 'active-low' else 'r'})
        self.first_samplenum = self.samplenum

        encoded = ""
        serialBin = 0
        buttonBin = 0

        # Keep getting samples for the period's middle and terminal edges.
        # At the same time that last sample starts the next period.
        while True:
            #logger.error("loop")

            keeloq_states = self.keeloq_states
            # Get the next two edges. Setup some variables that get
            # referenced in the calculation and in put() routines.
            start_samplenum = self.samplenum
            self.wait({0: 'e'})
            end_samplenum = self.samplenum
            self.wait({0: 'e'})
            self.ss_block = start_samplenum
            self.es_block = self.samplenum

            # Calculate the period, the duty cycle, and its ratio.
            period = self.samplenum - start_samplenum
            duty = end_samplenum - start_samplenum
            ratio = float(duty / period)
            # Report the period in units of time.
            period_t = float(period / self.samplerate)
            duty_t = float(duty / self.samplerate)

            # Report the duty cycle in the binary output.
            if (period_t >= 0.001 and period_t <= 0.0012):  #skip over Preamble and Header, allow last bit
                #logger.error("KLQ: setting state_bit = READ")
                keeloq_states.state_bit = "READ"
            elif keeloq_states.bit_count > 64 and duty_t < 0.0009 and keeloq_states.state_bit == 'READ':
                keeloq_states.state_bit = "FINISH"
            else:
                keeloq_states.state_bit = "START"

            if keeloq_states.state_bit == "READ" or keeloq_states.state_bit == "FINISH":
                #logger.error("KLQ: state_bit == READ")
                keeloq_states.bit_count = keeloq_states.bit_count + 1
                keeloq_states.bit = (0 if duty_t >= 0.0006 else 1)
                binary_s = str(keeloq_states.bit)
                if keeloq_states.state_bit == "FINISH":
                    logger.error("keeloq_states.state_bit == FINISH")
                    self.es_block = keeloq_states.bit_position + keeloq_states.last_period_samples
                self.put_bit(binary_s)
                if keeloq_states.state_bit == "READ":
                    keeloq_states.bit_position = self.samplenum #next bit starts here
                    keeloq_states.last_period_samples = period
                #logger.error("last_period_samples " + str(period))
                keeloq_states.next_byte = (keeloq_states.next_byte >> 1) | (keeloq_states.bit << 7)
                #logger.error("KLQ: keeloq_states.bit_count % 8: " + str(keeloq_states.bit_count % 8))
                if keeloq_states.bit_count % 8 == 0 :
                    keeloq_states.state_byte = "FINISH"
                else:
                    keeloq_states.state_byte = "READ"

            # ----------- state_byte --------------
            if keeloq_states.state_byte == "FINISH":
                #logger.error("KLQ: state_byte == FINISH")
                hex_byte = "{:02X}".format(keeloq_states.next_byte)
                self.put_hex("0x" + hex_byte)
                keeloq_states.byte_position = self.samplenum
                self.putb([0, keeloq_states.next_byte.to_bytes(1, byteorder='big')])
                
                keeloq_states.byte_count = keeloq_states.byte_count + 1
                keeloq_states.state_byte = "READ"
                keeloq_states.state_portion = "READ_BYTE"

            if keeloq_states.state_portion == "READ_BYTE":
                #logger.error("KLQ: state_portion == READ_BYTE")
                hex_byte = "{:02X}".format(keeloq_states.next_byte)
                if keeloq_states.byte_count <= 4:
                    encoded = hex_byte + encoded
                keeloq_states.next_byte = 0
                if keeloq_states.bit_count == 32:
                    self.put_portion("crypted: 0x" + encoded)
                    logger.error("crypted: " + encoded)
                    keeloq_states.portion_position = self.samplenum
                keeloq_states.state_portion = "SKIP"
                
            #28 bit serial is read bitwise because of bits non-byte-aligned size
            if keeloq_states.bit_count > 32 and keeloq_states.bit_count <= 60:
                #logger.error("KLQ: state_portion read serial bit_count:" + str(keeloq_states.bit_count))
                serialBin = serialBin | (keeloq_states.bit << (keeloq_states.bit_count - 33))
            if keeloq_states.bit_count == 60:
                serial = "serial: " + "0x{:02X}".format(serialBin)
                logger.error(serial)
                self.put_portion(serial)
                keeloq_states.portion_position = self.samplenum
                
            
            #4 bit button status
            if keeloq_states.bit_count > 60 and keeloq_states.bit_count <= 64:
                #logger.error("KLQ: state_portion read serial bit_count:" + str(keeloq_states.bit_count))
                buttonBin = buttonBin | (keeloq_states.bit << (keeloq_states.bit_count - 61))
            if keeloq_states.bit_count == 64:
                button = "button: " + "0x{:1X}".format(buttonBin)
                logger.error(button)
                self.put_portion(button)
                keeloq_states.portion_position = self.samplenum
            # ----------- state_bit --------------

            if keeloq_states.state_bit == "START" or keeloq_states.state_bit == "FINISH":
                #logger.error("KLQ: state_bit == START")
                keeloq_states.next_byte = 0
                keeloq_states.bit_count = 0
                keeloq_states.byte_count = 0
                keeloq_states.bit = 0
                keeloq_states.bit_position = self.samplenum
                keeloq_states.byte_position = self.samplenum
                keeloq_states.portion_position = self.samplenum
                keeloq_states.last_period_samples = 0;
                encoded = ""
                serialBin = 0
                buttonBin = 0

                

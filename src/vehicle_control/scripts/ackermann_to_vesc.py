#!/usr/bin/env python

import rospy
import numpy as np
from ackermann_msgs.msg import AckermannDriveStamped
from sensor_msgs.msg import Joy
from std_msgs.msg import Float64
import time

class AckermannToVesc:
    def __init__(self, dynamic_update = False, interval = 10):
        # Load configuration parameters for mapping and mode settings
        self.load_params()

        # Initialize mode as None indicating no mode is set initially
        self.mode = None

        # Initialize messages for publishing speed and servo position
        self.erpm_msg = Float64()
        self.servo_msg = Float64()
        
        # Initialize brake message
        self.brake_msg = Float64()
        self.brake_msg.data = self.brake_amps

        # Safety check parameters and subscriber
        self.speed_values = []  # Stores speed values for safety check
        n_seconds = 8  # Duration for the safety check in seconds
        hz = 40  # Expected number of speed values per second
        self.min_values = n_seconds * hz  # Minimum number of values for a valid safety check
        self.safety_sub = rospy.Subscriber('/rc/ackermann_cmd', AckermannDriveStamped, self.safety_check)
        
        # Driving command subscribers, initially not active
        self.rc_sub = None
        self.ad_sub = None
        
        # Joystick subscriber for mode updates
        self.joy_sub = rospy.Subscriber('/rc/joy', Joy, self.update_mode)

        # Publishers for motor speed and servo position
        self.erpm_pub = rospy.Publisher('/commands/motor/speed', Float64, queue_size=1)
        self.servo_pub = rospy.Publisher('/commands/servo/position', Float64, queue_size=1) 
        self.brake_pub = rospy.Publisher('/commands/motor/brake', Float64, queue_size=1) 
        
        # Inform user about safety check procedure
        info_msg = ("Please activate 'Deadman' mode. Do not touch the throttle or steering for {} seconds. "
                    "Safety check ends when speed stays at 0 m/s during this time.").format(n_seconds)
        rospy.loginfo(info_msg)

        # Update params with rospy.Timer real-time adjustments via Foxglove.
        if dynamic_update:
            # Using lambda to ignore TimerEvent arg from rospy.Timer callback
            rospy.Timer(rospy.Duration(interval), lambda e: self.load_params())
    
    def signal_calibration_complete(self):
        # Publish simple sinus sweep to indicate that the calibration was complete

        rate = rospy.Rate(40)
        
        amplitude = 0.2
        frequency = 3.0
        vertical_shift = 0.5

        t = np.linspace(0, np.pi, 60)
        values = amplitude * np.sin(frequency * t) + vertical_shift
        
        for value in values:
            self.servo_pub.publish(Float64(value))
            rate.sleep()
            
    
    def initialize_subscribers(self):
        """Initialize subscribers for manual and autonomous driving commands."""
        if self.rc_sub is None and self.ad_sub is None:
            self.rc_sub = rospy.Subscriber('/rc/ackermann_cmd', AckermannDriveStamped, lambda x: self.callback(x, self.manu_val))
            self.ad_sub = rospy.Subscriber('/autonomous/ackermann_cmd', AckermannDriveStamped, lambda x: self.callback(x, self.auto_val))

    def brake(self):
        # Publishes the brake message hz times in rapid succession, causing a blocking effect.
        hz = 420
        
        rate = rospy.Rate(hz)  

        for _ in range(hz):  # Publish 1s
            self.brake_pub.publish(self.brake_msg)
            rate.sleep()
        
    def update_mode(self, msg):
        """Update the driving mode based on joystick input."""
        new_mode = msg.buttons[self.mode_btn]
        
        if new_mode != self.mode: # mode change
            self.brake() # emergency brake
            
            self.mode = new_mode
            mode_name = {self.dead_val: "Deadman", self.auto_val: "Autonomous", self.manu_val: "Manual"}.get(self.mode, "Unknown")
            rospy.loginfo("Mode changed to: {}".format(mode_name))

    def safety_check(self, ackermann_msg):
        """Perform safety checks before enabling driving commands."""
        if self.mode != self.dead_val:
            return
        
        # Track vehicle speed to ensure it remains at 0 during the safety check
        speed = ackermann_msg.drive.speed
        self.speed_values.append(speed)
        if len(self.speed_values) > self.min_values:
            self.speed_values.pop(0)
            if max(self.speed_values) == 0 and min(self.speed_values) == 0:
                rospy.loginfo("Calibration complete!")
                self.safety_sub.unregister()  # Unsubscribe after passing the safety check
                self.signal_calibration_complete() # Publish simple sinus sweep to indicate that the calibration was complete
                self.initialize_subscribers()  # Activate driving command subscribers
                
                
    def callback(self, ackermann_msg, target):
           
        """Process received driving commands based on the current mode."""
        if self.mode != target:
            return

        # Convert Ackermann message to VESC-compatible commands
        steering_angle = ackermann_msg.drive.steering_angle
        speed = ackermann_msg.drive.speed
        erpm = self.speed_to_erpm_gain * speed
        servo_value = self.servo_mid + steering_angle * self.steer_to_servo_gain

        # Ensure servo commands are within limits
        self.servo_msg.data = max(min(servo_value, self.servo_max), self.servo_min)
        self.erpm_msg.data = erpm

        # Publish commands to VESC
        self.servo_pub.publish(self.servo_msg)
        

        if abs(erpm) < self.erpm_min:
            self.brake_pub.publish(self.brake_msg)
        else:
            self.erpm_pub.publish(self.erpm_msg)

    def load_params(self):
        self.servo_mid = rospy.get_param("/servo_mid")
        self.servo_max = rospy.get_param("/servo_max")
        self.servo_min = rospy.get_param("/servo_min")
        
        self.erpm_min = rospy.get_param("/erpm_min")
        self.brake_amps = rospy.get_param("/brake_amps")
        
        self.speed_to_erpm_gain = rospy.get_param("/speed_to_erpm_gain")
        self.steer_to_servo_gain = rospy.get_param("/steer_to_servo_gain")
        
        self.dead_val = rospy.get_param("/rc_dead_value")  # Deadman switch
        self.auto_val = rospy.get_param("/rc_auto_value")  # Drive autonomously
        self.manu_val = rospy.get_param("/rc_manu_value")  # Drive manually
        
        self.mode_btn = rospy.get_param("/rc_mode_button")

if __name__ == '__main__':
    rospy.init_node('ackermann_to_vesc', anonymous=True)
   
    A2V = AckermannToVesc(dynamic_update = False)

    try:
        rospy.spin()
    except KeyboardInterrupt:
        print("Shutting down ackermann_to_vesc")

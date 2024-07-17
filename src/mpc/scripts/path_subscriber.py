#!/usr/bin/env python3

import rospy
from nav_msgs.msg import Path
import transformations
import numpy as np
from ackermann_msgs.msg import AckermannDriveStamped
import rospkg
import sys
import os
import matplotlib.pyplot as plt
from qpsolvers import *
import time
from mpc.msg import Trajectory
#from cv_bridge import CvBridge, CvBridgeError
#import cv2
# The following line of code dynamically modifies the Python search path for modules at runtime by appending a specific directory.
# It uses the `rospkg` module, which provides an interface to ROS package locations, to find the directory of the 'mpc' package.
# Specifically, it appends the 'include' directory within the 'mpc' package to the `sys.path` list. This is necessary because the
# 'include' directory is where some Python modules or packages are located, but it's not a standard location that Python automatically
# searches for modules. By appending this directory to `sys.path`, it allows Python scripts to import modules from this non-standard location.
# In this case, after modifying `sys.path`, the script imports the `SupportFilesCar` module from the 'include' directory of the 'mpc' package.
sys.path.append(os.path.join(rospkg.RosPack().get_path('mpc'), 'include'))
from support_files_car_general import SupportFilesCar
#roslaunch mpc run_mpc.launch

# Node that subscribes to /path topic
class PathSubscriberNode(object):
    def __init__(self): 
        rospy.init_node('path_subscriber_node')


        # Subscribe to the /path topic
        self.path_sub = rospy.Subscriber('/path', Path, self.callback)

        # publish ackermann messages to VESC
        self.ackermann_pub = rospy.Publisher('/autonomous/ackermann_cmd', AckermannDriveStamped, queue_size=1) 
        self.trajectory_pub = rospy.Publisher('/trajectorympc', Trajectory, queue_size=1)

        #publish image trajectory visualization
        #self.image_pub = rospy.Publisher('/plot_image', Image, queue_size=1)

        # define messages
        self.ackMsg = AckermannDriveStamped()
        self.trajMsg = Trajectory()
        #self.bridge = CvBridge()
        self.counter = 1
        #Define MPC constants
        self.support=SupportFilesCar() #Load the Supportfile
        self.constants=self.support.constants #Set tge Constants Array
        self.Ts=self.constants['Ts']
        self.outputs=self.constants['outputs'] # number of outputs (psi, Y)
        self.hz = self.constants['hz'] # horizon prediction period
        self.time_length=self.constants['time_length'] # duration of the manoeuvre irrelvant
        self.inputs=self.constants['inputs'] # zahl
        self.x_lim=self.constants['x_lim']
        self.y_lim=self.constants['y_lim']
        self.trajectory=self.constants['trajectory'] # art der traj
        self.x_dot=1.#*8 #x_dot_ref[0]
        self.y_dot=0. #y_dot_ref[0]
        self.psi=0.#psi_ref[0]
        self.psi_dot=0.
        self.X=0.  #X_ref[0]
        self.Y=0.    #Y_ref[0]
        self.x_dot_dot=0.
        self.y_dot_dot=0.
        self.psi_dot_dot=0.
        self.a=0
        self.states=np.array([self.x_dot,self.y_dot,self.psi,self.psi_dot,self.X,self.Y])
        self.U1=0 # Input at t = -0.02 s (steering wheel angle in rad (delta))
        self.U2=0 # Input at t = -0.02 s (acceleration in m/s^2 (a))
        self.last_time = rospy.Time.now()


    def get_trajectory(self, msg):
        x_coords, y_coords, z_rotations = [], [], []

        #for pose_stamped in msg.poses: #für pathplanning
        for pose_stamped in msg.poses[1:]:
            # Extract x, y coordinates
            x = pose_stamped.pose.position.x
            y = pose_stamped.pose.position.y
            x_coords.append(x)
            y_coords.append(y)

            # Extract quaternion orientation
            quaternion = (
                pose_stamped.pose.orientation.x,
                pose_stamped.pose.orientation.y,
                pose_stamped.pose.orientation.z,
                pose_stamped.pose.orientation.w
            )

            # Convert quaternion to Euler angles
            euler = transformations.euler_from_quaternion(quaternion)

            # z-axis rotation in radians
            z_rotation = euler[2]
            z_rotations.append(z_rotation)
        
        return x_coords, y_coords, z_rotations

    def callback(self, msg):

        start = time.time()
        X_ref, Y_ref, psi_ref = self.get_trajectory(msg)
        print("get_trajectory: %.2f" % (time.time()-start))
        
        #Y_ref = [-x for x in Y_ref1]
        print("X_ref:",X_ref)
        X_ref_array = np.array(X_ref)
        Y_ref_array = np.array(Y_ref)
        Y_ref_array = Y_ref_array #* -1
        print("Y_ref:",Y_ref_array)# Calculate the turning angle of the car based on the delta
        

        # Calculate differences
        dX = X_ref_array[1:] - X_ref_array[:-1]
        dY = Y_ref_array[1:] - Y_ref_array[:-1]
        # Define the reference yaw angles
        psi=np.zeros(len(X_ref))
        psiInt=psi
        psi[0]=np.arctan2(dY[0],dX[0])
        psi[1:len(psi)]=np.arctan2(dY[0:len(dY)],dX[0:len(dX)])

        #  keep track the amount of rotations of the yaw angle
        dpsi=psi[1:len(psi)]-psi[0:len(psi)-1]
        psiInt[0]=psi[0]
        for i in range(1,len(psiInt)):
            if dpsi[i-1]<-np.pi:
                psiInt[i]=psiInt[i-1]+(dpsi[i-1]+2*np.pi)
            elif dpsi[i-1]>np.pi:
                psiInt[i]=psiInt[i-1]+(dpsi[i-1]-2*np.pi)
            else:
                psiInt[i]=psiInt[i-1]+dpsi[i-1]
        #print("psi",psiInt)
        x_dot_ref = np.ones_like(X_ref_array)
        y_dot_ref = np.zeros_like(X_ref_array)
        
        refSignals=np.zeros(len(X_ref)*self.outputs)
        k=0
        
        for i in range(0,len(refSignals),self.outputs):
            refSignals[i]=x_dot_ref[k]
            refSignals[i+1]=psiInt[k]
            refSignals[i+2]=X_ref_array[k]
            refSignals[i+3]=Y_ref_array[k]
            k=k+1
        #print("Ref_Signals",refSignals)
        #print("states aktuell",self.states)

        accelerations=np.array([self.x_dot_dot,self.y_dot_dot,self.psi_dot_dot])
        du=np.zeros((self.inputs*self.hz,1)) # zwei lösungen

        # Generate the discrete state space matrices
        Ad,Bd,Cd,Dd=self.support.state_space(self.states,self.U1,self.U2)

        # Generate the augmented current state and the reference vector
        x_aug_t=np.transpose([np.concatenate((self.states,[self.U1,self.U2]),axis=0)])
        states_predicted = np.zeros_like(x_aug_t)
        r = refSignals

        Hdb,Fdbt,Cdb,Adc,G,ht,states_predicted=self.support.mpc_simplification(Ad,Bd,Cd,Dd,self.hz,x_aug_t,du)
        #print("Pred states",states_predicted)
        #print(f"{Fdbt.shape=}")
        #print(f"{np.concatenate((np.transpose(x_aug_t)[0][0:len(x_aug_t)],r),axis=0).shape=}")
        #print(f"{r.shape=}")
        #print(f"{np.transpose(x_aug_t).shape=}")
        #print(f"{np.transpose(x_aug_t)[0].shape=}")
        #print(f"{np.transpose(x_aug_t)[0][0:len(x_aug_t)].shape=}")
        ft=np.matmul(np.concatenate((np.transpose(x_aug_t)[0][0:len(x_aug_t)],r),axis=0),Fdbt)
        #Fdbt.shape=(28, 10)
        #np.concatenate((np.transpose(x_aug_t)[0][0:len(x_aug_t)],r),axis=0).shape=(24,)
        #Fdbt.shape=(28, 10)
        #np.concatenate((np.transpose(x_aug_t)[0][0:len(x_aug_t)],r),axis=0).shape=(28,)
        #r.shape=(20,)
        #np.transpose(x_aug_t).shape=(1, 8)
        #np.transpose(x_aug_t)[0].shape=(8,)
        #np.transpose(x_aug_t)[0][0:len(x_aug_t)].shape=(8,)
        #r.shape=(16,) ANDERS

        start = time.time()
        try:
            du=solve_qp(Hdb,ft,G,ht,solver="clarabel") #clarabel cvxopt highs
            du=np.transpose([du])
            print("du",du)
            # exit()
        except ValueError as ve:
            print("Test",Hdb)
            print(ft)
            print(G)
            print(ht)
            print(Adc)
            print(x_aug_t)
            print(du)
            print(i)
            #break
        print("solve_qp: %.2f" % (time.time()-start))

        ## Visualisierung ##
        Xges = np.zeros(len(du)//2)
        Yges = np.zeros(len(du)//2)
        U1_pred = self.U1
        U2_pred = self.U2
        
        for i in range(len(du)//2):

            U1_pred = U1_pred+du[i*2][0]
            U2_pred = U2_pred+du[i*2+1][0]
            if i == 0:
                states_pred=self.states
            states_pred,xpp,ypp,psipp=self.support.open_loop_new_states_pred(states_pred,U1_pred,U2_pred)
            X=states_pred[4]#x und y müssen in funktion noch auf nicht 0 gesetzt werden
            Y=states_pred[5]
            Xges[i] = X 
            Yges[i] = Y 
        print("xges", Xges)  
        print("yges", Yges)  
        ## Pub von X_ref,Y_ref,Xges,Yges
        #self.trajMsg.Xref = X_ref 
        #self.trajMsg.Yref = Y_ref
        #self.trajMsg.Xges = Xges
        #self.trajMsg.Yges = Yges
        
        #self.trajectory_pub.publish(self.trajMsg)
        
        try:
            # Create directory if it does not exist
            base_dir = rospkg.RosPack().get_path('mpc')

        
        # Define the full file path
            save_path = os.path.join(base_dir, f'comparison_plot_{self.counter}.png')
            print(f"Save path set to: {save_path}")
     

        # Plotting the graph
            plt.figure(figsize=(5, 3))
            plt.plot(X_ref, Y_ref, label='Reference')
            plt.plot(Xges, Yges, label='Generated')
            plt.xlabel('X-axis')
            plt.ylabel('Y-axis')
            plt.ylim(-0.8, 0.8)
            plt.title('Comparison of Reference and Generated')
            plt.legend()
            plt.draw()
            plt.savefig(save_path)
            print(f"Plot saved to: {save_path}")
            plt.clf()
            plt.close()
            self.counter += 1
            
        except Exception as e:
            print(f"Error in callback: {e}")
        
    

        # Convert plot to image
        #plt.tight_layout()
        #fig = plt.gcf()
        #fig.canvas.draw()
        #img = np.fromstring(fig.canvas.tostring_rgb(), dtype=np.uint8, sep='')
        #img = img.reshape(fig.canvas.get_width_height()[::-1] + (3,))
        
        # Convert RGB to BGR
        #img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        #image_msg = self.bridge.cv2_to_imgmsg(img, encoding="bgr8")
        #self.image_pub.publish(image_msg)
        #plt.close()
        
        self.U1=self.U1+du[0][0]  #steeringwheel angle  du ist die aenderung
        self.U2=self.U2+du[1][0]  #acceleration
        print("Lenkwinkel:", self.U1)
        print("Beschl:",self.U2)
        
            
        self.states,self.x_dot_dot,self.y_dot_dot,self.psi_dot_dot=self.support.open_loop_new_states(self.states,self.U1,self.U2) #calculate new states 
        # Einfuegen von x punkt, y punkt ueber beschl sensor, sowie psi dot 
        #print("States",self.states)
        
      
        #Integration von U2 von Beschl auf Geschw
        #current_time = rospy.Time.now()
        #dt = (current_time - self.last_time).to_sec()
        #self.last_time = current_time
        #speed += self.acceleration * dt
        # Nachricht an VESC senden
        steering_angle = self.U1  #psi_ref[1] / 4 # platzhalter
        speed = 1.0 # die sinus kurve wurde mit 1 m/s generiert


        self.ackMsg.header.stamp = rospy.Time.now()
        self.ackMsg.drive.steering_angle = steering_angle
        self.ackMsg.drive.speed = speed

        self.ackermann_pub.publish(self.ackMsg)

    def spin(self):
        # Keep the node running
        rospy.spin()

if __name__ == '__main__':
    try:
        node = PathSubscriberNode()
        node.spin()
    except rospy.ROSInterruptException:
        pass

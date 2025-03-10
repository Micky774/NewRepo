from __future__ import print_function
import argparse
import torch
import torch.utils.data
import time
from torch import nn, optim
from torch.nn import functional as F
from torch.nn import LeakyReLU
from torchvision import datasets, transforms
from torchvision.utils import save_image
from torchsummary import summary
from torch.autograd import Variable
from sklearn.manifold import TSNE
from sklearn.cluster import DBSCAN
from torch.utils.tensorboard import SummaryWriter

import math
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cbook as cbook
import matplotlib.colors as colors

startTime = time.time()

writer = SummaryWriter()

parser = argparse.ArgumentParser(description='VAE MNIST Example')
parser.add_argument('--batch-size', type=int, default=128, metavar='B',
                    help='input batch size for training (default: 128)')
parser.add_argument('--epochs', type=int, default=10, metavar='E',
                    help='number of epochs to train (default: 10)')
parser.add_argument('--no-cuda', action='store_true', default=False,
                    help='enables CUDA training')
parser.add_argument('--seed', type=int, default=1, metavar='S',
                    help='random seed (default: 1)')
parser.add_argument('--log-interval', type=int, default=10, metavar='N',
                    help='how many batches to wait before logging training status')
parser.add_argument('--save', type=str, default='', metavar='s',
                    help='saves the weights to a given filepath')
parser.add_argument('--load', type=str, default='', metavar='l',
                    help='loads the weights from a given filepath')
parser.add_argument('--beta', type=float, default=1.0, metavar='b',
                    help='sets the value of beta for a beta-vae implementation')
parser.add_argument('--pseudos', type=int, default=10, metavar='p',
                    help='Number of pseudo-inputs (default: 10)')
parser.add_argument('--lsdim', type = int, default=2, metavar='ld',
                    help='sets the number of dimensions in the latent space. should be >1. If  <3, will generate graphical representation of latent without TSNE projection')

args = parser.parse_args()
args.cuda = not args.no_cuda and torch.cuda.is_available()
if(args.cuda):
    with torch.cuda.device(0):
        torch.tensor([1.]).cuda()

torch.manual_seed(args.seed)

device = torch.device("cuda" if args.cuda else "cpu")

kwargs = {'num_workers': 1, 'pin_memory': True} if args.cuda else {}
train_loader = torch.utils.data.DataLoader(
    datasets.MNIST('../data', train=True, download=True,
                   transform=transforms.ToTensor()),
    batch_size=args.batch_size, shuffle=True, **kwargs)
test_loader = torch.utils.data.DataLoader(
    datasets.MNIST('../data', train=False, transform=transforms.ToTensor()),
    batch_size=args.batch_size, shuffle=True, **kwargs)
    
"""
Secpmd Convolutional Neural Network Variational Autoencoder with Transpose Convolutional Decoder
Uses 4 convolutional hidden layers in the encoder before encoding a distribution
Applies 1 fully-connected and 3 transpose convolutional hidden layers to code before output layer.

@author Davis Jackson & Quinn Wyner
"""
    
    
class VAE(nn.Module):
    def __init__(self):
        super(VAE, self).__init__()

        #(1,28,28) -> (8,26,26)
        self.conv1 = nn.Conv2d(1, 8, 3)
        
        #(8,13,13) -> (16,12,12)
        self.conv2 = nn.Conv2d(8, 16, 2)
        
        #(16,6,6) -> (32,4,4)
        self.conv3 = nn.Conv2d(16, 32, 3)
        
        #(32,4,4) -> (64,2,2)
        self.conv4 = nn.Conv2d(32, 64, 3)

        #(80,4,4) -> lsdim mean and logvar
        self.mean = nn.Linear(64*2*2, args.lsdim)
        self.logvar = nn.Linear(64*2*2, args.lsdim)

        self.means = nn.Linear(args.pseudos, 28*28, bias=False)
        
        '''
        #(2 -> 4)
        self.fc1 = nn.Linear(args.lsdim, 4)
        #reshape elsewhere
        
        #(1,2,2) -> (32,7,7)
        self.convt1 = nn.ConvTranspose2d(1, 32, 6)
        
        #(32,7,7) -> (16, 14, 14)
        self.convt2 = nn.ConvTranspose2d(32, 16, 8)

        #(16, 14, 14) -> (8, 20, 20)
        self.convt3 = nn.ConvTranspose2d(16, 8, 7)

        #(8,20, 20) -> (1, 28, 28) 
        self.convt4 = nn.ConvTranspose2d(8, 1, 9)
        '''
        #Size-Preserving Convolution
        self.conv5 = nn.Conv2d(args.lsdim + 2, 64, 3, padding=1)
        #Size-Preserving Convolution
        self.conv6 = nn.Conv2d(64, 64, 3, padding=1)
        #Size-Preserving Convolution
        self.conv7 = nn.Conv2d(64, 64, 3, padding=1)
        #Size-Preserving Convolution
        self.conv8 = nn.Conv2d(64, 64, 3, padding=1)
        #Channel-Reducing Convolution
        self.conv9 = nn.Conv2d(64, 1, 1)

        #add pseudo-inputs
        self.means = nn.Linear(args.pseudos, 28*28, bias=False)

        # create an idle input for calling pseudo-inputs
        self.idle_input = torch.eye(args.pseudos,args.pseudos,requires_grad=True)
        self.idle_input = self.idle_input.cuda()


    def reconstruct_x(self, x):
        x_mean, _, _, _ = self.forward(x)
        return x_mean
    
    # ADDITIONAL METHODS
    def generate_x(self, N=args.pseudos):
        means = self.means(self.idle_input)[0:N]
        z_sample_gen_mean, z_sample_gen_logvar = self.q_z(means)
        z_sample_rand = self.reparameterize(z_sample_gen_mean, z_sample_gen_logvar)

        samples_rand, _ = self.p_x(z_sample_rand)
        return samples_rand
        
    # THE MODEL: VARIATIONAL POSTERIOR
    def q_z(self, x):
        #(1,28,28) -> (8,26,26) -> (8,13,13)
        x = F.max_pool2d(F.leaky_relu(self.conv1(x)), (2,2))
        
        #(8,13,13) -> (16,12,12) -> (16,6,6)
        x = F.max_pool2d(F.leaky_relu(self.conv2(x)), (2,2))
        
        #(16,6,6) -> (32,4,4)
        x = F.leaky_relu(self.conv3(x))
        
        #(32,4,4) -> (64,2,2)
        x = F.leaky_relu(self.conv4(x))

        x=x.view(-1, 64*2*2)
        
        z_q_mean = self.mean(x)
        z_q_logvar = self.logvar(x)
        return z_q_mean, z_q_logvar

    def reparameterize(self, mu, logvar):
        std = torch.exp(0.5*logvar)
        eps = torch.randn_like(std)
        return eps.mul(std).add_(mu)

    # p(x|z)
    # THE MODEL: GENERATIVE DISTRIBUTION
    def p_x(self, z):
        
        baseVector = z.view(-1, args.lsdim, 1, 1)
        base = baseVector.repeat(1,1,28,28)

        stepTensor = torch.linspace(-1, 1, 28).to(device)

        xAxisVector = stepTensor.view(1,1,28,1)
        yAxisVector = stepTensor.view(1,1,1,28)

        xPlane = xAxisVector.repeat(z.shape[0],1,1,28)
        yPlane = yAxisVector.repeat(z.shape[0],1,28,1)

        base = torch.cat((xPlane, yPlane, base), 1)         

        x = F.leaky_relu(self.conv5(base))
        x = F.leaky_relu(self.conv6(x))
        x = F.leaky_relu(self.conv7(x))
        x = F.leaky_relu(self.conv8(x))
        x = F.leaky_relu(self.conv9(x))
        '''
        x = LeakyReLU(0.1)(self.fc1(z))
        x = x.view(-1,1,2,2)
        x = LeakyReLU(0.1)(self.convt1(x))
        x = LeakyReLU(0.1)(self.convt2(x))
        x = LeakyReLU(0.1)(self.convt3(x))
        x = LeakyReLU(0.1)(self.convt4(x))
        '''
        return x
            
    def log_p_z(self,z):
        C = args.pseudos

        print
        # calculate params
        X = F.leaky_relu(self.means(self.idle_input))

        # calculate params for given data
        z_p_mean, z_p_logvar = self.q_z(X.view(-1,1,28,28))  # C x M

        # expand z
        z_expand = z.unsqueeze(1)
        means = z_p_mean.unsqueeze(0)
        logvars = z_p_logvar.unsqueeze(0)

        a = log_Normal_diag(z_expand, means, logvars, dim=2) - math.log(C)  # MB x C
        a_max, _ = torch.max(a, 1)  # MB x 1

        # calculte log-sum-exp
        log_prior = a_max + torch.log(torch.sum(torch.exp(a - a_max.unsqueeze(1)), 1))  # MB x 1
        return torch.sum(log_prior, 0)
    
    def forward(self, x):

        #z~q(z|x)
        mu, logvar = self.q_z(x)
        z=self.reparameterize(mu,logvar)

        x_mean=self.p_x(z)
        #decode code
        return x_mean, mu, logvar, z

    
    
        
    # Reconstruction + KL divergence losses summed over all elements and batch
    def loss_function(self, recon_x, x, mu, logvar, z_q):
        RE = F.mse_loss(recon_x.view(-1,784), x.view(-1, 784), reduction = 'sum')

        # see Appendix B from VAE paper:
        # Kingma and Welling. Auto-Encoding Variational Bayes. ICLR, 2014
        # https://arxiv.org/abs/1312.6114
        # 0.5 * sum(1 + log(sigma^2) - mu^2 - sigma^2)

        
        
        
        # KL
        log_p_z = self.log_p_z(z_q)
        log_q_z = torch.sum(log_Normal_diag(z_q, mu, logvar, dim=1),0)
        KL = -(log_p_z - log_q_z)

        return RE + args.beta*KL



model = VAE().to(device)
optimizer = optim.Adam(model.parameters(), lr=1e-3)

def log_Normal_diag(x, mean, log_var, average=False, dim=None):

    log_normal = -0.5 * ( log_var + torch.pow( x - mean, 2 ) / torch.exp( log_var ) )

    if average:

        return torch.mean( log_normal, dim )

    else:

        return torch.sum( log_normal, dim )
        
        
def train(epoch):
    model.train()
    train_loss = 0
    for batch_idx, (data, _) in enumerate(train_loader):
        data = data.to(device)
        optimizer.zero_grad()
        recon_batch, mu, logvar, z = model(data)
        loss = model.loss_function(recon_batch, data, mu, logvar, z)
        loss.backward()
        train_loss += loss.item()
        optimizer.step()
        step=epoch*len(train_loader)+batch_idx
        writer.add_scalar('loss',loss.item(),global_step=step)
        if batch_idx % args.log_interval == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                epoch, batch_idx * len(data), len(train_loader.dataset),
                100. * batch_idx / len(train_loader),
                loss.item() / len(data)))

    print('====> Epoch: {} Average loss: {:.4f}'.format(
          epoch, train_loss / len(train_loader.dataset)))
 

def test(epoch, max, startTime):
    model.eval()
    test_loss = 0
    zTensor = torch.empty(0,args.lsdim).to(device)
    labelTensor = torch.empty(0, dtype = torch.long)
    with torch.no_grad():
        for i, (data, _) in enumerate(test_loader):
            data = data.to(device)
            recon_batch, mu, logvar, z = model(data)
            test_loss += model.loss_function(recon_batch, data, mu, logvar,z).item()
            zTensor = torch.cat((zTensor, z), 0)
            labelTensor = torch.cat((labelTensor, _), 0)
    test_loss /= len(test_loader.dataset)
    print('====> Test set loss: {:.4f}'.format(test_loss))
    if(epoch == max):
        print("--- %s seconds ---" % (time.time() - startTime))
        if device == torch.device("cuda"):
            z1 = torch.Tensor.cpu(zTensor[:, 0]).numpy()
            z2 = torch.Tensor.cpu(zTensor[:, 1]).numpy()
        else:
            z1 = zTensor[:, 0].numpy()
            z2 = zTensor[:, 0].numpy()
        cmap = colors.ListedColormap(['#e6194B', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#fabebe'])
        
        #Handling different dimensionalities
        if (args.lsdim < 3) :
            z1 = torch.Tensor.cpu(zTensor[:, 0]).numpy()
            z2 = torch.Tensor.cpu(zTensor[:, 1]).numpy()
            scatterPlot = plt.scatter(z1, z2, s = 4, c = labelTensor, cmap = cmap) #Regular 2dim plot, RE-ADD CMAP = CMAP
            plt.colorbar()
        elif (args.lsdim == 3) :
            fig=plt.figure()
            ax=fig.gca(projection='3d')
            z1 = torch.Tensor.cpu(zTensor[:, 0]).numpy()
            z2 = torch.Tensor.cpu(zTensor[:, 1]).numpy()
            z3 = torch.Tensor.cpu(zTensor[:, 2]).numpy()
            scatterPlot = ax.scatter(z1, z2, z3, s = 4, c = labelTensor, cmap = cmap) #Regular 3dim plot
        else:    
            Z_embedded = TSNE(n_components=2, verbose=1).fit_transform(zTensor.cpu())        
            z1 = Z_embedded[:, 0]
            z2 = Z_embedded[:, 1]
            scatterPlot = plt.scatter(z1, z2, s = 4, c = labelTensor, cmap = cmap) #TSNE projection for >3dim 
            plt.colorbar()

        plt.show()
        
def dplot(x):
    img = p_x(x)
    plt.imshow(img)

if __name__ == "__main__":
    summary(model,(1,28,28))
    if(args.load == ''):
        for epoch in range(1, args.epochs + 1):
            train(epoch)
            test(epoch, args.epochs, startTime)
    else:
        model.load_state_dict(torch.load(args.load))
        test(args.epochs, args.epochs, startTime)
    if(args.save != ''):
        torch.save(model.state_dict(), args.save)

    temp =model.means(model.idle_input).view(-1,28,28).detach().cpu()

    res = torch.autograd.Variable(torch.Tensor(1,1,28,28), requires_grad=True).to(device)
    #writer.add_graph(model,res,verbose=True)
    
    writer.close()
        
    '''
    for x in range(args.pseudos):
        plt.matshow(temp[x].numpy())
        plt.show()
    '''